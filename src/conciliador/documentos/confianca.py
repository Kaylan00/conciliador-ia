"""Confiança da extração, calculada por regras auditáveis.

Cada regra que falha vira um Problema com código, mensagem para quem revisa e penalidade, e
confianca = max(0, 1 - soma das penalidades). Problemas que o extrator já trouxe (ex.: "extracao_invalida" do
modelo) entram na mesma soma. Ninguém pergunta ao modelo o quanto ele confia em si mesmo.
"""

from __future__ import annotations

import re
import unicodedata
from decimal import Decimal

from rapidfuzz import fuzz

from conciliador.extracao.cnpj import cnpj_valido, formatar_cnpj
from conciliador.extracao.extenso import valor_por_extenso_no_texto
from conciliador.extracao.normalizacao import formatar_valor_br, valor_aparece_no_texto
from conciliador.modelos import DocumentoExtraido, OrigemTexto, Problema, TipoDocumento

PENALIDADES = {
    "valor_ausente": 0.5,  # sem valor não há o que conciliar
    "cnpj_invalido": 0.3,
    "valor_nao_encontrado_no_texto": 0.3,  # valor que não está escrito no documento é palpite
    "valor_diferente_do_extenso": 0.3,  # algarismos e extenso discordam: um dos dois foi mal lido
    "total_diferente_dos_itens": 0.3,
    # Nota e fatura sempre trazem o CNPJ de quem emite; sem ele, o emitente sai só de um nome, que pode estar errado.
    "cnpj_emitente_ausente": 0.3,
    # Sozinha já leva a confiança para baixo de 0,8, a linha de corte da revisão: no teste de estresse, imagem
    # borrada a ponto de o Tesseract duvidar de si mesmo trouxe valor e data errados que nenhuma outra regra pegou.
    "ocr_baixa_confianca": 0.3,
    # Visto com o gpt-4o-mini: no recibo "Recebi de <empresa>", o modelo pôs a empresa que PAGOU como emitente.
    # Isso inverte a direção (compra vira venda) e nenhuma outra regra pegava.
    "recibo_emitente_e_quem_pagou": 0.5,
    "data_emissao_ausente": 0.2,
    "emitente_ausente": 0.2,
}
CONFIANCA_OCR_MINIMA = 70.0  # escala do Tesseract, 0 a 100
TIPOS_COM_CNPJ_DO_EMITENTE = {TipoDocumento.NFE, TipoDocumento.NFSE, TipoDocumento.FATURA}  # recibo pode não ter
TOLERANCIA_ITENS = Decimal("0.01")  # arredondamento de centavo em item com quantidade fracionada
SIMILARIDADE_MESMO_NOME = 85
_RECEBI_DE = re.compile(r"receb(?:i|emos)\s+d[eoa]s?\s+(.+?)(?:,|\s+a\s+(?:importancia|quantia)|\s+o\s+valor|\n|$)")


def _sem_acento(texto: str) -> str:
    return unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode().lower()


def quem_pagou_no_recibo(texto: str) -> str | None:
    """Em recibo, quem assina recebeu; quem aparece depois de "Recebi de" é quem pagou."""
    achado = _RECEBI_DE.search(_sem_acento(texto))
    return achado.group(1).strip(" .:") if achado else None


def _problema(codigo: str, mensagem: str) -> Problema:
    return Problema(codigo=codigo, mensagem=mensagem, penalidade=PENALIDADES[codigo])


def problemas_da_extracao(doc: DocumentoExtraido, confianca_ocr: float | None = None) -> list[Problema]:
    problemas = []
    for papel, cnpj in (("emitente", doc.emitente_cnpj), ("destinatário", doc.destinatario_cnpj)):
        if cnpj and not cnpj_valido(cnpj):
            problemas.append(_problema(
                "cnpj_invalido",
                f"O CNPJ do {papel} ({formatar_cnpj(cnpj)}) não fecha o dígito verificador: provável erro de leitura.",
            ))

    if doc.valor_total is None:
        problemas.append(_problema("valor_ausente", "Não foi possível achar o valor total do documento."))
    elif doc.origem != OrigemTexto.XML:
        if not valor_aparece_no_texto(doc.valor_total, doc.texto):
            problemas.append(_problema(
                "valor_nao_encontrado_no_texto",
                f"O valor R$ {formatar_valor_br(doc.valor_total)} não aparece escrito no texto do documento.",
            ))
        extenso = valor_por_extenso_no_texto(doc.texto)
        if extenso is not None and extenso != doc.valor_total:
            problemas.append(_problema(
                "valor_diferente_do_extenso",
                f"O valor em algarismos (R$ {formatar_valor_br(doc.valor_total)}) não bate com o valor escrito por "
                f"extenso (R$ {formatar_valor_br(extenso)}).",
            ))

    if doc.itens and doc.valor_total is not None:
        soma = sum((item.valor_total for item in doc.itens), Decimal("0"))
        if abs(soma - doc.valor_total) > TOLERANCIA_ITENS:
            problemas.append(_problema(
                "total_diferente_dos_itens",
                f"Os itens somam R$ {formatar_valor_br(soma)}, mas o total é R$ {formatar_valor_br(doc.valor_total)}.",
            ))

    if doc.data_emissao is None:
        problemas.append(_problema("data_emissao_ausente", "Não foi possível achar a data de emissão."))
    if not doc.emitente_nome and not doc.emitente_cnpj:
        problemas.append(_problema("emitente_ausente", "Não foi possível identificar quem emitiu o documento."))
    elif not doc.emitente_cnpj and doc.tipo in TIPOS_COM_CNPJ_DO_EMITENTE:
        problemas.append(_problema(
            "cnpj_emitente_ausente",
            f"{doc.tipo.value.upper()} sempre traz o CNPJ de quem emite, mas ele não foi encontrado: "
            "confira o emitente.",
        ))
    if doc.tipo == TipoDocumento.RECIBO and doc.emitente_nome:
        pagador = quem_pagou_no_recibo(doc.texto)
        if pagador and fuzz.token_set_ratio(_sem_acento(doc.emitente_nome), pagador) >= SIMILARIDADE_MESMO_NOME:
            problemas.append(_problema(
                "recibo_emitente_e_quem_pagou",
                f"O recibo diz que {doc.emitente_nome} pagou, mas ele saiu como emitente: em recibo, o emitente é "
                "quem recebeu o dinheiro. Confira emitente e direção.",
            ))
    if confianca_ocr is not None and confianca_ocr < CONFIANCA_OCR_MINIMA:
        problemas.append(_problema(
            "ocr_baixa_confianca",
            f"A leitura da imagem saiu com confiança média de {confianca_ocr:.0f}% "
            f"(mínimo {CONFIANCA_OCR_MINIMA:.0f}%): confira os campos no arquivo original.",
        ))
    return problemas


def aplicar_confianca(doc: DocumentoExtraido, confianca_ocr: float | None = None) -> DocumentoExtraido:
    problemas = [*doc.problemas, *problemas_da_extracao(doc, confianca_ocr)]
    soma = sum(p.penalidade for p in problemas)
    return doc.model_copy(update={"problemas": problemas, "confianca": round(max(0.0, 1.0 - soma), 4)})
