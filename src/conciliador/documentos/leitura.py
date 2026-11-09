"""Entrada do módulo: bytes de um arquivo viram um DocumentoExtraido com direção e confiança calculadas."""

from __future__ import annotations

import re
from enum import StrEnum
from pathlib import PurePath

from conciliador.documentos.confianca import aplicar_confianca
from conciliador.documentos.nfe_xml import ler_nfe, parece_nfe
from conciliador.documentos.ocr import ResultadoOCR, abrir_imagem, ler_imagem
from conciliador.documentos.pdf import renderizar_paginas, textos_das_paginas
from conciliador.extracao import Extrator, extrator_padrao
from conciliador.extracao.cnpj import somente_digitos
from conciliador.modelos import Direcao, DocumentoExtraido, OrigemTexto

# Página com menos letras e dígitos que isso é tratada como escaneada (às vezes sobra só o número da página).
MINIMO_CARACTERES_TEXTO = 30


class Formato(StrEnum):
    XML_NFE = "xml_nfe"
    PDF = "pdf"
    IMAGEM = "imagem"


class FormatoNaoSuportado(ValueError):
    """Arquivo que o módulo não sabe ler."""


_ASSINATURAS_IMAGEM = (b"\x89PNG\r\n\x1a\n", b"\xff\xd8\xff", b"II*\x00", b"MM\x00*", b"BM")
_EXTENSOES = {".xml": Formato.XML_NFE, ".pdf": Formato.PDF, **dict.fromkeys(
    (".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp"), Formato.IMAGEM)}


def detectar_formato(conteudo: bytes, nome_arquivo: str) -> Formato:
    """Pela assinatura do conteúdo primeiro (extensão mente); a extensão só desempata quando não há assinatura."""
    inicio = conteudo[:1024]
    if b"%PDF-" in inicio:
        return Formato.PDF
    if inicio.startswith(_ASSINATURAS_IMAGEM) or (inicio[:4] == b"RIFF" and inicio[8:12] == b"WEBP"):
        return Formato.IMAGEM
    if inicio.lstrip(b"\xef\xbb\xbf \t\r\n").startswith(b"<"):
        if parece_nfe(conteudo):
            return Formato.XML_NFE
        raise FormatoNaoSuportado(f"{nome_arquivo}: XML que não é NF-e (NFS-e em XML ainda não é lida)")
    if formato := _EXTENSOES.get(PurePath(nome_arquivo).suffix.lower()):
        return formato
    raise FormatoNaoSuportado(f"{nome_arquivo}: formato não reconhecido (aceita XML de NF-e, PDF e imagem)")


def definir_direcao(emitente_cnpj: str | None, cnpj_empresa: str | None) -> Direcao:
    """Do ponto de vista da empresa dona dos livros: se ela emitiu, é venda (receber); senão, compra (pagar)."""
    if cnpj_empresa and emitente_cnpj and somente_digitos(cnpj_empresa) == somente_digitos(emitente_cnpj):
        return Direcao.RECEBER
    return Direcao.PAGAR


def ler_documento(
    conteudo: bytes,
    nome_arquivo: str,
    cnpj_empresa: str | None = None,
    extrator: Extrator | None = None,
) -> DocumentoExtraido:
    """Lê XML de NF-e, PDF (com texto ou escaneado) ou imagem e devolve os campos com confiança calculada.

    `cnpj_empresa` é o CNPJ de quem está fazendo a escrituração e define a direção. `extrator` vale para PDF e
    imagem (o XML já é estruturado); sem ele, usa `extrator_padrao()` (variável EXTRATOR).
    """
    formato = detectar_formato(conteudo, nome_arquivo)
    confianca_ocr = None
    if formato is Formato.XML_NFE:
        documento = ler_nfe(conteudo)
    else:
        texto, origem, confianca_ocr = _texto_do_arquivo(conteudo, formato)
        documento = (extrator or extrator_padrao()).extrair(texto, origem)
    documento = documento.model_copy(update={"direcao": definir_direcao(documento.emitente_cnpj, cnpj_empresa)})
    return aplicar_confianca(documento, confianca_ocr)


def _pouco_texto(texto: str) -> bool:
    return len(re.sub(r"\W", "", texto)) < MINIMO_CARACTERES_TEXTO


def _texto_do_arquivo(conteudo: bytes, formato: Formato) -> tuple[str, OrigemTexto, float | None]:
    """Texto, origem e confiança média do OCR (None quando não houve OCR)."""
    if formato is Formato.IMAGEM:
        resultado = ler_imagem(abrir_imagem(conteudo))
        return resultado.texto, OrigemTexto.OCR, resultado.confianca_media

    paginas = textos_das_paginas(conteudo)
    escaneadas = [indice for indice, texto in enumerate(paginas) if _pouco_texto(texto)]
    if not escaneadas:
        return "\n".join(paginas), OrigemTexto.PDF_TEXTO, None
    # Decide por página: um PDF pode juntar páginas digitais e páginas escaneadas.
    lidas = {indice: ler_imagem(imagem) for indice, imagem in renderizar_paginas(conteudo, escaneadas)}
    texto = "\n".join(lidas[i].texto if i in lidas else pagina for i, pagina in enumerate(paginas))
    return texto, OrigemTexto.OCR, ResultadoOCR.juntar(list(lidas.values())).confianca_media
