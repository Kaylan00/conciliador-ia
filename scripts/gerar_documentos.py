"""Gera os documentos de exemplo descritos em dados/exemplos/cenario.json.

Cada documento do cenário vira um arquivo no formato pedido em `formato`:

- xml_nfe: XML de NF-e 4.00 (nfeProc) com emitente, destinatário, itens, totais e duplicata;
- pdf_texto: PDF com camada de texto, no layout de NFS-e ou de fatura de consumo;
- pdf_escaneado: o mesmo layout renderizado como imagem torta e ruidosa, num PDF sem texto;
- imagem_escaneada: recibo em JPG/PNG com cara de foto (rotação, sombra, ruído, compressão).

A aleatoriedade vem de uma semente fixa por documento, então rodar de novo produz os mesmos arquivos
(no mesmo ambiente) e eles podem ficar versionados. O gerador não importa nada de `conciliador`: ele faz
o papel do mundo lá fora, e o leitor é que precisa se virar com o que ele produz.

    python scripts/gerar_documentos.py
"""

from __future__ import annotations

import argparse
import functools
import io
import itertools
import json
import textwrap
import zlib
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from xml.etree import ElementTree as ET

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

RAIZ = Path(__file__).resolve().parents[1]
CENARIO = RAIZ / "dados" / "exemplos" / "cenario.json"
SAIDA = RAIZ / "dados" / "exemplos" / "documentos"

SEMENTE = 20261001
NS_NFE = "http://www.portalfiscal.inf.br/nfe"
FUSO_BRASILIA = timezone(timedelta(hours=-3))
A4 = (210.0, 297.0)
A5_DEITADO = (210.0, 148.0)


# --------------------------------------------------------------------------- cadastro fictício


@dataclass(frozen=True)
class Cadastro:
    endereco: str
    bairro: str
    municipio: str
    uf: str = "ES"
    cep: str = "29000-000"
    codigo_municipio: str = "3205309"
    inscricao_estadual: str = "ISENTO"
    inscricao_municipal: str = ""


CADASTROS = {
    "52601815000120": Cadastro("Rua Gama Rosa, 215", "Centro", "Vitória", cep="29015-120",
                               inscricao_estadual="083456120", inscricao_municipal="0712345"),
    "90830166000128": Cadastro("Rodovia do Contorno, 1800", "Carapina", "Serra", cep="29161-002",
                               codigo_municipio="3205002", inscricao_estadual="082114573"),
    "13186091000143": Cadastro("Rua Sete de Setembro, 410, sala 802", "Centro", "Vitória", cep="29015-000",
                               inscricao_municipal="0458123"),
    "39099603000128": Cadastro("Av. Nossa Senhora da Penha, 1200", "Praia do Canto", "Vitória", cep="29055-131",
                               inscricao_estadual="081234567"),
    "08246281000179": Cadastro("Av. Champagnat, 911", "Centro", "Vila Velha", cep="29100-011",
                               codigo_municipio="3205200"),
    "94821993000106": Cadastro("Rua Duque de Caxias, 77", "Centro", "Vila Velha", cep="29100-040",
                               codigo_municipio="3205200", inscricao_estadual="082776311"),
    "51819093000111": Cadastro("Rua Graciano Neves, 52", "Centro", "Vitória", cep="29015-330"),
    "78657975000184": Cadastro("Rua Chapot Presvot, 389", "Praia do Canto", "Vitória", cep="29055-410",
                               inscricao_municipal="0533817"),
    "43231948000160": Cadastro("Av. Expedito Garcia, 500", "Campo Grande", "Cariacica", cep="29146-201",
                               codigo_municipio="3201308", inscricao_estadual="082905447"),
    "75749118000116": Cadastro("Av. Dante Michelini, 1500", "Jardim Camburi", "Vitória", cep="29090-430",
                               inscricao_municipal="0398772"),
}
CADASTRO_PADRAO = Cadastro("Rua Professor Baltazar, 120", "Centro", "Vitória", cep="29020-090")


def cadastro(cnpj: str | None) -> Cadastro:
    return CADASTROS.get(cnpj or "", CADASTRO_PADRAO)


# Conteúdo próprio de cada documento que o cenário não descreve (o cenário só guarda o gabarito).
SERVICOS_NFSE = {
    "doc-02": "17.19 - Contabilidade, inclusive serviços técnicos e auxiliares",
    "doc-08": "1.05 - Licenciamento ou cessão de direito de uso de programas de computação",
    "doc-10": "14.09 - Alfaiataria e costura, quando o material for fornecido pelo usuário final",
}


RECIBOS_PESSOA_FISICA = {"doc-03"}  # quem assina é pessoa física: "Recebi" em vez de "Recebemos"


@dataclass(frozen=True)
class DetalheFatura:
    titulo: str
    rotulo_total: str
    linhas_detalhe: tuple[str, ...]
    itens: tuple[tuple[str, str], ...]  # (descrição, valor)
    linha_digitavel: str | None = None


FATURAS = {
    "doc-04": DetalheFatura(
        titulo="CONTA DE ENERGIA ELÉTRICA",
        rotulo_total="Total a pagar",
        linhas_detalhe=(
            "Instalação: 3001245789    Classe: Comercial trifásico    Referência: 09/2026",
            "Leitura anterior: 31/08/2026    Leitura atual: 30/09/2026    Consumo: 412 kWh",
        ),
        itens=(
            ("Consumo de energia elétrica - 412 kWh", "360.50"),
            ("Adicional de bandeira tarifária amarela", "7.77"),
            ("Contribuição de iluminação pública - CIP", "44.10"),
        ),
        linha_digitavel="83660000004-1 12370138000-2 00092026100-4 04587100000-7",
    ),
    "doc-05": DetalheFatura(
        titulo="FATURA DE SERVIÇOS DE INTERNET",
        rotulo_total="Valor a pagar",
        linhas_detalhe=(
            "Plano: Fibra 500 Mb    Código do cliente: 77120    Referência: outubro/2026",
        ),
        itens=(("Internet fibra 500 Mb - outubro/2026", "129.90"),),
    ),
}


# --------------------------------------------------------------------------- formatação


def formatar_cnpj(cnpj: str) -> str:
    return f"{cnpj[:2]}.{cnpj[2:5]}.{cnpj[5:8]}/{cnpj[8:12]}-{cnpj[12:]}"


def formatar_valor(valor: Decimal | str) -> str:
    """Decimal('1234.5') -> '1.234,50'."""
    return f"{Decimal(valor):,.2f}".replace(",", "_").replace(".", ",").replace("_", ".")


def formatar_data(dia: date | str) -> str:
    return (date.fromisoformat(dia) if isinstance(dia, str) else dia).strftime("%d/%m/%Y")


MESES = ("janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho", "agosto", "setembro", "outubro",
         "novembro", "dezembro")


def data_por_extenso(dia: date) -> str:
    return f"{dia.day} de {MESES[dia.month - 1]} de {dia.year}"


_UNIDADES = ("", "um", "dois", "três", "quatro", "cinco", "seis", "sete", "oito", "nove", "dez", "onze", "doze",
             "treze", "quatorze", "quinze", "dezesseis", "dezessete", "dezoito", "dezenove")
_DEZENAS = ("", "", "vinte", "trinta", "quarenta", "cinquenta", "sessenta", "setenta", "oitenta", "noventa")
_CENTENAS = ("", "cento", "duzentos", "trezentos", "quatrocentos", "quinhentos", "seiscentos", "setecentos",
             "oitocentos", "novecentos")


def _ate_999(numero: int) -> str:
    if numero == 100:
        return "cem"
    centena, resto = divmod(numero, 100)
    partes = [_CENTENAS[centena]] if centena else []
    if 0 < resto < 20:
        partes.append(_UNIDADES[resto])
    elif resto:
        dezena, unidade = divmod(resto, 10)
        partes.append(_DEZENAS[dezena] + (f" e {_UNIDADES[unidade]}" if unidade else ""))
    return " e ".join(partes)


def _inteiro_por_extenso(numero: int) -> str:
    if not 0 < numero < 1_000_000:
        raise ValueError(f"fora do intervalo suportado: {numero}")
    milhar, resto = divmod(numero, 1000)
    if not milhar:
        return _ate_999(resto)
    mil = "mil" if milhar == 1 else f"{_ate_999(milhar)} mil"
    if not resto:
        return mil
    # "mil e oitocentos", mas "mil cento e cinquenta": o "e" só entra antes de centena redonda ou de < 100.
    conector = " e " if resto < 100 or resto % 100 == 0 else " "
    return f"{mil}{conector}{_ate_999(resto)}"


def valor_por_extenso(valor: Decimal | str) -> str:
    """Decimal('57.80') -> 'cinquenta e sete reais e oitenta centavos'."""
    valor = Decimal(valor)
    reais = int(valor)
    centavos = int((valor - reais) * 100)
    partes = []
    if reais:
        partes.append(f"{_inteiro_por_extenso(reais)} {'real' if reais == 1 else 'reais'}")
    if centavos:
        partes.append(f"{_inteiro_por_extenso(centavos)} {'centavo' if centavos == 1 else 'centavos'}")
    return " e ".join(partes)


def gerador_aleatorio(doc_id: str) -> np.random.Generator:
    """Semente por documento: incluir um documento novo no cenário não muda os arquivos dos outros."""
    return np.random.default_rng([SEMENTE, zlib.crc32(doc_id.encode())])


def horario(rng: np.random.Generator) -> time:
    return time(int(rng.integers(8, 18)), int(rng.integers(0, 60)), int(rng.integers(0, 60)))


# --------------------------------------------------------------------------- NF-e (XML)


def _dv_modulo_11(numero: str) -> str:
    pesos = itertools.cycle(range(2, 10))
    resto = sum(int(d) * p for d, p in zip(reversed(numero), pesos, strict=False)) % 11
    return "0" if resto < 2 else str(11 - resto)


def _sub(pai: ET.Element, tag: str, texto: str | None = None, **atributos: str) -> ET.Element:
    elemento = ET.SubElement(pai, f"{{{NS_NFE}}}{tag}", atributos)
    if texto is not None:
        elemento.text = texto
    return elemento


def _endereco(pai: ET.Element, tag: str, dados: Cadastro) -> None:
    logradouro, _, numero = dados.endereco.rpartition(", ")
    numero, _, complemento = numero.partition(", ")
    endereco = _sub(pai, tag)
    _sub(endereco, "xLgr", logradouro or dados.endereco)
    _sub(endereco, "nro", numero or "S/N")
    if complemento:
        _sub(endereco, "xCpl", complemento)
    _sub(endereco, "xBairro", dados.bairro)
    _sub(endereco, "cMun", dados.codigo_municipio)
    _sub(endereco, "xMun", dados.municipio)
    _sub(endereco, "UF", dados.uf)
    _sub(endereco, "CEP", dados.cep.replace("-", ""))
    _sub(endereco, "cPais", "1058")
    _sub(endereco, "xPais", "Brasil")


def _ncm_e_unidade(descricao: str) -> tuple[str, str]:
    texto = descricao.lower()
    if "linho" in texto and "tecido" in texto:
        return "53091100", "M"
    if "tricoline" in texto and "camisa" not in texto:
        return "52083200", "M"
    if "linha" in texto:
        return "54011011", "CX"
    if "botão" in texto:
        return "96062100", "PCT"
    if "vestido" in texto:
        return "62044900", "UN"
    if "camisa" in texto:
        return "62052000", "UN"
    return "00000000", "UN"


def gerar_nfe_xml(doc: dict, rng: np.random.Generator) -> bytes:
    ET.register_namespace("", NS_NFE)
    emissao = datetime.combine(date.fromisoformat(doc["data_emissao"]), horario(rng), FUSO_BRASILIA)
    numero = int(doc["numero"])
    codigo_numerico = f"{int(rng.integers(0, 10**8)):08d}"
    chave_sem_dv = f"32{emissao:%y%m}{doc['emitente_cnpj']}55001{numero:09d}1{codigo_numerico}"
    dv = _dv_modulo_11(chave_sem_dv)
    chave = chave_sem_dv + dv
    venda_propria = doc["direcao"] == "receber"

    raiz = ET.Element(f"{{{NS_NFE}}}nfeProc", {"versao": "4.00"})
    nfe = _sub(raiz, "NFe")
    inf = _sub(nfe, "infNFe", versao="4.00", Id=f"NFe{chave}")

    ide = _sub(inf, "ide")
    emitente = cadastro(doc["emitente_cnpj"])
    for tag, valor in (
        ("cUF", "32"), ("cNF", codigo_numerico), ("natOp", "Venda de mercadoria"), ("mod", "55"), ("serie", "1"),
        ("nNF", str(numero)), ("dhEmi", emissao.isoformat()), ("tpNF", "1"), ("idDest", "1"),
        ("cMunFG", emitente.codigo_municipio), ("tpImp", "1"), ("tpEmis", "1"), ("cDV", dv), ("tpAmb", "2"),
        ("finNFe", "1"), ("indFinal", "0"), ("indPres", "9"), ("procEmi", "0"), ("verProc", "conciliador-ia 0.1"),
    ):
        _sub(ide, tag, valor)

    emit = _sub(inf, "emit")
    _sub(emit, "CNPJ", doc["emitente_cnpj"])
    _sub(emit, "xNome", doc["emitente_nome"])
    _endereco(emit, "enderEmit", emitente)
    _sub(emit, "IE", emitente.inscricao_estadual)
    _sub(emit, "CRT", "1")

    dest = _sub(inf, "dest")
    destinatario = cadastro(doc["destinatario_cnpj"])
    _sub(dest, "CNPJ", doc["destinatario_cnpj"])
    _sub(dest, "xNome", doc["destinatario_nome"])
    _endereco(dest, "enderDest", destinatario)
    _sub(dest, "indIEDest", "1")
    _sub(dest, "IE", destinatario.inscricao_estadual)

    for indice, item in enumerate(doc["itens"], start=1):
        det = _sub(inf, "det", nItem=str(indice))
        prod = _sub(det, "prod")
        ncm, unidade = _ncm_e_unidade(item["descricao"])
        quantidade = f"{Decimal(item['quantidade']):.4f}"
        unitario = f"{Decimal(item['valor_unitario']):.10f}"
        for tag, valor in (
            ("cProd", f"{indice:03d}"), ("cEAN", "SEM GTIN"), ("xProd", item["descricao"]), ("NCM", ncm),
            ("CFOP", "5101" if venda_propria else "5102"), ("uCom", unidade), ("qCom", quantidade),
            ("vUnCom", unitario), ("vProd", f"{Decimal(item['valor_total']):.2f}"), ("cEANTrib", "SEM GTIN"),
            ("uTrib", unidade), ("qTrib", quantidade), ("vUnTrib", unitario), ("indTot", "1"),
        ):
            _sub(prod, tag, valor)
        imposto = _sub(det, "imposto")
        icms = _sub(_sub(imposto, "ICMS"), "ICMSSN102")
        _sub(icms, "orig", "0")
        _sub(icms, "CSOSN", "102")
        _sub(_sub(_sub(imposto, "PIS"), "PISNT"), "CST", "07")
        _sub(_sub(_sub(imposto, "COFINS"), "COFINSNT"), "CST", "07")

    total = f"{Decimal(doc['valor_total']):.2f}"
    icms_tot = _sub(_sub(inf, "total"), "ICMSTot")
    for tag in ("vBC", "vICMS", "vICMSDeson", "vFCP", "vBCST", "vST", "vFCPST", "vFCPSTRet"):
        _sub(icms_tot, tag, "0.00")
    _sub(icms_tot, "vProd", total)
    for tag in ("vFrete", "vSeg", "vDesc", "vII", "vIPI", "vIPIDevol", "vPIS", "vCOFINS", "vOutro"):
        _sub(icms_tot, tag, "0.00")
    _sub(icms_tot, "vNF", total)

    _sub(_sub(inf, "transp"), "modFrete", "9")

    cobr = _sub(inf, "cobr")
    fat = _sub(cobr, "fat")
    for tag, valor in (("nFat", str(numero)), ("vOrig", total), ("vDesc", "0.00"), ("vLiq", total)):
        _sub(fat, tag, valor)
    dup = _sub(cobr, "dup")
    _sub(dup, "nDup", "001")
    _sub(dup, "dVenc", doc["data_vencimento"])
    _sub(dup, "vDup", total)

    a_vista = doc["data_vencimento"] == doc["data_emissao"]
    det_pag = _sub(_sub(inf, "pag"), "detPag")
    _sub(det_pag, "indPag", "0" if a_vista else "1")
    _sub(det_pag, "tPag", "17" if a_vista else "15")  # 17 = PIX, 15 = boleto
    _sub(det_pag, "vPag", total)

    _sub(_sub(inf, "infAdic"), "infCpl", "Documento fictício gerado para testes do conciliador-ia. Sem valor fiscal.")

    recebimento = emissao + timedelta(seconds=int(rng.integers(5, 120)))
    inf_prot = _sub(_sub(raiz, "protNFe", versao="4.00"), "infProt")
    for tag, valor in (
        ("tpAmb", "2"), ("verAplic", "SVRS202610011200"), ("chNFe", chave), ("dhRecbto", recebimento.isoformat()),
        ("nProt", f"332{emissao:%y}{int(rng.integers(0, 10**10)):010d}"), ("cStat", "100"),
        ("xMotivo", "Autorizado o uso da NF-e"),
    ):
        _sub(inf_prot, tag, valor)

    ET.indent(raiz, space="  ")
    corpo = ET.tostring(raiz, encoding="unicode")
    return f'<?xml version="1.0" encoding="UTF-8"?>\n{corpo}\n'.encode()


# --------------------------------------------------------------------------- layout de página


@dataclass(frozen=True)
class Texto:
    x: float  # mm a partir da esquerda
    y: float  # mm a partir do topo, na linha de base do texto
    conteudo: str
    tamanho: float = 9.0  # pt
    negrito: bool = False
    alinhar: str = "esquerda"  # "esquerda" | "direita" | "centro"


@dataclass(frozen=True)
class Caixa:
    x: float
    y: float
    largura: float
    altura: float
    fundo: bool = False


@dataclass(frozen=True)
class Traco:
    x1: float
    y1: float
    x2: float
    y2: float


@dataclass(frozen=True)
class Pagina:
    largura: float
    altura: float
    elementos: tuple[Texto | Caixa | Traco, ...]


def layout_nfse(doc: dict, rng: np.random.Generator) -> Pagina:
    prestador, tomador = cadastro(doc["emitente_cnpj"]), cadastro(doc["destinatario_cnpj"])
    emissao = datetime.combine(date.fromisoformat(doc["data_emissao"]), horario(rng))
    verificacao = "".join(rng.choice(list("ABCDEFGHJKLMNPQRSTUVWXYZ23456789"), 8))
    valor = Decimal(doc["valor_total"])
    iss = (valor * Decimal("0.02")).quantize(Decimal("0.01"))

    caixas = [Caixa(12, 12, 186, 36), Caixa(12, 52, 186, 30), Caixa(12, 86, 186, 30), Caixa(12, 120, 186, 44),
              Caixa(12, 168, 186, 24), Caixa(12, 196, 186, 14, fundo=True), Caixa(12, 214, 186, 24)]
    textos = [
        Texto(16, 20, f"PREFEITURA MUNICIPAL DE {prestador.municipio.upper()}", 11, True),
        Texto(16, 26, "Secretaria Municipal da Fazenda", 9),
        Texto(16, 40, "NOTA FISCAL DE SERVIÇOS ELETRÔNICA - NFS-e", 12, True),
        Texto(148, 19, "Número da NFS-e", 7.5),
        Texto(148, 25, doc["numero"], 11, True),
        Texto(148, 31, "Data e hora de emissão", 7.5),
        Texto(148, 36, f"{emissao:%d/%m/%Y %H:%M:%S}", 9),
        Texto(148, 41, "Código de verificação", 7.5),
        Texto(148, 45.5, f"{verificacao[:4]}-{verificacao[4:]}", 9),
    ]
    for topo, titulo, nome, cnpj, dados in (
        (52, "PRESTADOR DE SERVIÇOS", doc["emitente_nome"], doc["emitente_cnpj"], prestador),
        (86, "TOMADOR DE SERVIÇOS", doc["destinatario_nome"], doc["destinatario_cnpj"], tomador),
    ):
        textos += [
            Texto(16, topo + 6, titulo, 9, True),
            Texto(16, topo + 12, f"Razão social: {nome}", 9),
            Texto(16, topo + 18, f"CNPJ: {formatar_cnpj(cnpj)}", 9),
            Texto(110, topo + 18, f"Inscrição municipal: {dados.inscricao_municipal or 'não informada'}", 9),
            Texto(16, topo + 24, f"Endereço: {dados.endereco} - {dados.bairro} - {dados.municipio}/{dados.uf} - "
                                 f"CEP {dados.cep}", 9),
        ]
    textos.append(Texto(16, 126, "DISCRIMINAÇÃO DOS SERVIÇOS", 9, True))
    y = 132.0
    for item in doc["itens"]:
        textos.append(Texto(16, y, item["descricao"], 9))
        textos.append(Texto(16, y + 5, f"Quantidade: {Decimal(item['quantidade']):.0f}    Valor unitário: R$ "
                                       f"{formatar_valor(item['valor_unitario'])}", 8.5))
        y += 12
    textos.append(Texto(16, 158, f"Serviço (LC 116/2003): {SERVICOS_NFSE.get(doc['id'], 'não informado')}", 8))

    colunas = (("Valor dos serviços", f"R$ {formatar_valor(valor)}"), ("Deduções", "R$ 0,00"),
               ("Base de cálculo", f"R$ {formatar_valor(valor)}"), ("Alíquota", "2,00%"),
               ("Valor do ISS", f"R$ {formatar_valor(iss)}"))
    for indice, (rotulo, _) in enumerate(colunas):
        textos.append(Texto(16 + indice * 37, 175, rotulo, 7.5))
    for indice, (_, conteudo) in enumerate(colunas):
        textos.append(Texto(16 + indice * 37, 182, conteudo, 9))
    textos.append(Texto(16, 188.5, "ISS devido ao município do prestador. Optante pelo Simples Nacional.", 7.5))
    textos.append(Texto(16, 205, f"VALOR TOTAL DA NOTA = R$ {formatar_valor(valor)}", 12, True))
    textos += [
        Texto(16, 220, "OUTRAS INFORMAÇÕES", 9, True),
        Texto(16, 226, f"Vencimento: {formatar_data(doc['data_vencimento'])}. Pagamento por boleto bancário ou PIX.",
              9),
        Texto(16, 232, "Documento fictício gerado para testes do conciliador-ia. Sem valor fiscal.", 8),
    ]
    return Pagina(*A4, tuple(caixas + textos))


def layout_fatura(doc: dict, rng: np.random.Generator) -> Pagina:
    detalhe = FATURAS[doc["id"]]
    soma = sum(Decimal(valor) for _, valor in detalhe.itens)
    if soma != Decimal(doc["valor_total"]):
        raise ValueError(f"{doc['id']}: itens somam {soma}, cenário diz {doc['valor_total']}")
    emitente, cliente = cadastro(doc["emitente_cnpj"]), cadastro(doc["destinatario_cnpj"])

    caixas = [Caixa(122, 12, 76, 30), Caixa(12, 48, 186, 30), Caixa(12, 84, 186, 22), Caixa(12, 112, 186, 50),
              Caixa(12, 168, 186, 16, fundo=True)]
    textos = [
        Texto(14, 20, doc["emitente_nome"], 15, True),
        Texto(14, 27, f"CNPJ {formatar_cnpj(doc['emitente_cnpj'])}    Inscrição estadual {emitente.inscricao_estadual}",
              8.5),
        Texto(14, 32, f"{emitente.endereco} - {emitente.bairro} - {emitente.municipio}/{emitente.uf}", 8.5),
        Texto(14, 37, "Central de atendimento: 0800 725 0196", 8.5),
        Texto(125, 20, detalhe.titulo, 9, True),
        Texto(125, 27, f"Fatura nº {doc['numero']}", 9),
        Texto(125, 33, f"Data de emissão: {formatar_data(doc['data_emissao'])}", 9),
        Texto(16, 54, "DADOS DO CLIENTE", 8, True),
        Texto(16, 60, f"Cliente: {doc['destinatario_nome']}", 9),
        Texto(16, 66, f"CNPJ: {formatar_cnpj(doc['destinatario_cnpj'])}", 9),
        Texto(16, 72, f"Endereço: {cliente.endereco} - {cliente.bairro} - {cliente.municipio}/{cliente.uf} - "
                      f"CEP {cliente.cep}", 9),
    ]
    y = 91.0
    for linha in (*detalhe.linhas_detalhe, f"Referente a: {doc['descricao']}"):
        textos.append(Texto(16, y, linha, 9))
        y += 5.5
    textos += [Texto(16, 119, "Descrição", 8.5, True), Texto(194, 119, "Valor (R$)", 8.5, True, "direita")]
    y = 126.0
    for descricao, valor in detalhe.itens:
        textos += [Texto(16, y, descricao, 9), Texto(194, y, formatar_valor(valor), 9, alinhar="direita")]
        y += 6
    textos += [
        Texto(16, 178, f"Vencimento: {formatar_data(doc['data_vencimento'])}", 12, True),
        Texto(104, 178, f"{detalhe.rotulo_total}: R$ {formatar_valor(doc['valor_total'])}", 12, True),
        Texto(16, 192, "Pagável em qualquer banco, casa lotérica ou pelo aplicativo até a data de vencimento.", 8),
        Texto(16, 197, "Documento fictício gerado para testes do conciliador-ia. Sem valor fiscal.", 8),
    ]
    elementos: list[Texto | Caixa | Traco] = [*caixas, *textos]
    if detalhe.linha_digitavel:
        elementos += [Traco(12, 206, 198, 206), Texto(16, 213, "Linha digitável", 7.5),
                      Texto(16, 219, detalhe.linha_digitavel, 11)]
    return Pagina(*A4, tuple(elementos))


def layout_recibo(doc: dict, empresa: dict) -> Pagina:
    valor = formatar_valor(doc["valor_total"])
    descricao = doc["descricao"][0].lower() + doc["descricao"][1:]
    pessoa_fisica = doc["id"] in RECIBOS_PESSOA_FISICA
    verbo = "Recebi" if pessoa_fisica else "Recebemos"
    if doc["destinatario_nome"]:
        pagador = f" de {doc['destinatario_nome']}"
        if doc["destinatario_cnpj"]:
            pagador += f", CNPJ {formatar_cnpj(doc['destinatario_cnpj'])},"
    else:
        pagador = ""
    frase = (f"{verbo}{pagador} a importância de R$ {valor} ({valor_por_extenso(doc['valor_total'])}), "
             f"referente a {descricao}.")
    emissao = date.fromisoformat(doc["data_emissao"])
    # O recibo de pessoa física (aluguel) vem de modelo de papelaria: data por extenso. Os de comércio, dd/mm/aaaa.
    quando = data_por_extenso(emissao) if pessoa_fisica else formatar_data(emissao)
    cidade = cadastro(empresa["cnpj"]).municipio

    elementos: list[Texto | Caixa | Traco] = [
        Caixa(8, 8, 194, 132),
        Caixa(150, 15, 46, 13),
        Texto(105, 26, "RECIBO", 20, True, "centro"),
        Texto(192, 24.5, f"R$ {valor}", 13, True, "direita"),
    ]
    if doc["numero"]:
        elementos.append(Texto(16, 24.5, f"Nº {doc['numero']}", 11))
    y = 46.0
    for linha in textwrap.wrap(frase, width=72):
        elementos.append(Texto(16, y, linha, 11.5))
        y += 8.5
    elementos.append(Texto(16, y + 8, f"{cidade}, {quando}.", 11.5))
    assinatura = y + 32
    elementos += [Traco(55, assinatura, 155, assinatura), Texto(105, assinatura + 6, doc["emitente_nome"], 11, False,
                                                                 "centro")]
    if doc["emitente_cnpj"]:
        elementos.append(Texto(105, assinatura + 12, f"CNPJ {formatar_cnpj(doc['emitente_cnpj'])}", 10, False,
                               "centro"))
    return Pagina(*A5_DEITADO, tuple(elementos))


# --------------------------------------------------------------------------- renderização


def desenhar_pdf(pagina: Pagina, titulo: str) -> bytes:
    """PDF com camada de texto. `invariant` tira data e id aleatórios do arquivo para ele ser reprodutível."""
    saida = io.BytesIO()
    pdf = canvas.Canvas(saida, pagesize=(pagina.largura * mm, pagina.altura * mm), invariant=1)
    pdf.setTitle(titulo)
    pdf.setAuthor("conciliador-ia (dados fictícios)")
    pdf.setLineWidth(0.6)
    for elemento in pagina.elementos:
        match elemento:
            case Caixa(x, y, largura, altura, fundo):
                pdf.setFillGray(0.9)
                pdf.rect(x * mm, (pagina.altura - y - altura) * mm, largura * mm, altura * mm, stroke=1,
                         fill=int(fundo))
            case Traco(x1, y1, x2, y2):
                pdf.line(x1 * mm, (pagina.altura - y1) * mm, x2 * mm, (pagina.altura - y2) * mm)
            case Texto(x, y, conteudo, tamanho, negrito, alinhar):
                pdf.setFillGray(0)
                pdf.setFont("Helvetica-Bold" if negrito else "Helvetica", tamanho)
                desenhar = {"esquerda": pdf.drawString, "direita": pdf.drawRightString,
                            "centro": pdf.drawCentredString}[alinhar]
                desenhar(x * mm, (pagina.altura - y) * mm, conteudo)
    pdf.showPage()
    pdf.save()
    return saida.getvalue()


@functools.cache
def _fonte(negrito: bool, tamanho_px: int) -> ImageFont.FreeTypeFont:
    nome = "DejaVuSans-Bold.ttf" if negrito else "DejaVuSans.ttf"
    try:
        return ImageFont.truetype(nome, tamanho_px)
    except OSError as erro:
        raise SystemExit(f"fonte {nome} não encontrada: rode o gerador na imagem conciliador-dev "
                         "(fonts-dejavu-core) para os arquivos saírem iguais aos versionados") from erro


def desenhar_imagem(pagina: Pagina, dpi: int) -> Image.Image:
    def px(valor_mm: float) -> int:
        return round(valor_mm / 25.4 * dpi)

    imagem = Image.new("L", (px(pagina.largura), px(pagina.altura)), 255)
    tela = ImageDraw.Draw(imagem)
    espessura = max(1, px(0.25))
    for elemento in pagina.elementos:
        match elemento:
            case Caixa(x, y, largura, altura, fundo):
                tela.rectangle((px(x), px(y), px(x + largura), px(y + altura)), outline=0,
                               fill=230 if fundo else None, width=espessura)
            case Traco(x1, y1, x2, y2):
                tela.line((px(x1), px(y1), px(x2), px(y2)), fill=0, width=espessura)
            case Texto(x, y, conteudo, tamanho, negrito, alinhar):
                ancora = {"esquerda": "ls", "direita": "rs", "centro": "ms"}[alinhar]
                fonte = _fonte(negrito, round(tamanho / 72 * dpi))
                tela.text((px(x), px(y)), conteudo, fill=0, font=fonte, anchor=ancora)
    return imagem


PAPEL = (247, 244, 236)
MESA = (92, 84, 76)


def envelhecer(imagem: Image.Image, rng: np.random.Generator, *, angulo: float, ruido: float, desfoque: float,
               sombra: float, fundo: tuple[int, int, int] | None = None) -> Image.Image:
    """Dá cara de scan/foto: papel amarelado, sombra de um lado, rotação, desfoque e ruído de sensor.

    Com `fundo`, a folha gira dentro de uma área maior dessa cor (foto em cima da mesa); sem ele, gira dentro do
    próprio quadro, como num scanner ou num app que já recortou a folha.
    """
    tons = np.asarray(imagem.convert("L"), dtype=np.float32)[..., None] / 255.0
    cor = tons * (np.array(PAPEL, dtype=np.float32) / 255.0)
    if sombra:
        altura, largura = tons.shape[:2]
        direcao = rng.uniform(0, 2 * np.pi)
        campo = (np.cos(direcao) * np.linspace(0, 1, largura)[None, :]
                 + np.sin(direcao) * np.linspace(0, 1, altura)[:, None])
        campo = (campo - campo.min()) / (campo.max() - campo.min())
        cor = cor * (1 - sombra * campo)[..., None]
    resultado = Image.fromarray(np.clip(cor * 255, 0, 255).astype(np.uint8), "RGB")
    resultado = resultado.rotate(angulo, resample=Image.Resampling.BICUBIC, expand=fundo is not None,
                                 fillcolor=fundo or PAPEL)
    resultado = resultado.filter(ImageFilter.GaussianBlur(desfoque))
    pixels = np.asarray(resultado, dtype=np.float32)
    pixels += rng.normal(0, ruido, pixels.shape[:2])[..., None]
    return Image.fromarray(np.clip(pixels, 0, 255).astype(np.uint8), "RGB")


def _angulo(rng: np.random.Generator, minimo: float, maximo: float) -> float:
    return float(rng.uniform(minimo, maximo) * rng.choice([-1, 1]))


# --------------------------------------------------------------------------- um gerador por formato


def gerar_pdf_texto(doc: dict, rng: np.random.Generator, empresa: dict) -> bytes:
    layout = layout_nfse if doc["tipo"] == "nfse" else layout_fatura
    return desenhar_pdf(layout(doc, rng), titulo=Path(doc["arquivo"]).stem)


def gerar_pdf_escaneado(doc: dict, rng: np.random.Generator, empresa: dict) -> bytes:
    layout = layout_nfse if doc["tipo"] == "nfse" else layout_fatura
    dpi = 200
    pagina = layout(doc, rng)
    scan = envelhecer(desenhar_imagem(pagina, dpi), rng, angulo=_angulo(rng, 0.6, 1.5), ruido=9, desfoque=0.7,
                      sombra=0.0).convert("L")
    jpeg = io.BytesIO()
    scan.save(jpeg, "JPEG", quality=70)
    jpeg.seek(0)
    # Página só com a imagem, sem nenhum texto: é o que sai de um scanner sem OCR embutido.
    saida = io.BytesIO()
    pdf = canvas.Canvas(saida, pagesize=(pagina.largura * mm, pagina.altura * mm), invariant=1)
    pdf.setTitle(Path(doc["arquivo"]).stem)
    pdf.drawImage(ImageReader(jpeg), 0, 0, width=pagina.largura * mm, height=pagina.altura * mm)
    pdf.showPage()
    pdf.save()
    return saida.getvalue()


def branquear_fundo(imagem: Image.Image, preto: int = 60, branco: int = 190) -> Image.Image:
    """Curva de níveis dos apps de scanner do celular: fundo vira branco, tinta vira preto, sobra ruído."""
    tons = np.asarray(imagem.convert("L"), dtype=np.float32)
    return Image.fromarray(np.clip((tons - preto) * 255 / (branco - preto), 0, 255).astype(np.uint8), "L")


def gerar_imagem_escaneada(doc: dict, rng: np.random.Generator, empresa: dict) -> bytes:
    recibo = desenhar_imagem(layout_recibo(doc, empresa), dpi=200)
    angulo = _angulo(rng, 1.0, 3.0)
    saida = io.BytesIO()
    if Path(doc["arquivo"]).suffix.lower() == ".png":
        # Foto passada por app de scanner: folha já recortada e fundo branqueado.
        foto = envelhecer(recibo, rng, angulo=angulo, ruido=10, desfoque=0.8, sombra=0.14)
        branquear_fundo(foto).save(saida, "PNG", optimize=True)
    else:
        # Foto crua do celular: folha em cima da mesa, sombra e compressão JPEG.
        foto = envelhecer(recibo, rng, angulo=angulo, ruido=10, desfoque=0.8, sombra=0.14, fundo=MESA)
        foto.save(saida, "JPEG", quality=72)
    return saida.getvalue()


GERADORES: dict[str, Callable[[dict, np.random.Generator, dict], bytes]] = {
    "xml_nfe": lambda doc, rng, empresa: gerar_nfe_xml(doc, rng),
    "pdf_texto": gerar_pdf_texto,
    "pdf_escaneado": gerar_pdf_escaneado,
    "imagem_escaneada": gerar_imagem_escaneada,
}


def gerar(pasta: Path = SAIDA, cenario: Path = CENARIO) -> list[Path]:
    dados = json.loads(cenario.read_text(encoding="utf-8"))
    pasta.mkdir(parents=True, exist_ok=True)
    caminhos = []
    for doc in dados["documentos"]:
        conteudo = GERADORES[doc["formato"]](doc, gerador_aleatorio(doc["id"]), dados["empresa"])
        caminho = pasta / doc["arquivo"]
        caminho.write_bytes(conteudo)
        caminhos.append(caminho)
    return caminhos


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--saida", type=Path, default=SAIDA, help="pasta onde gravar os arquivos")
    argumentos = parser.parse_args()
    for caminho in gerar(argumentos.saida):
        print(f"{caminho.stat().st_size:>9,} bytes  {caminho.name}")


if __name__ == "__main__":
    main()
