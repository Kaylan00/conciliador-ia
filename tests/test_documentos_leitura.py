import io
import json
from datetime import date
from decimal import Decimal
from pathlib import Path

import httpx
import pytest
from PIL import Image, ImageDraw, ImageFont
from reportlab.lib.pagesizes import A4
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

from conciliador.documentos import Formato, FormatoNaoSuportado, detectar_formato, ler_documento
from conciliador.documentos.ocr import ImagemInvalida
from conciliador.documentos.pdf import PdfInvalido
from conciliador.extracao import ExtratorLLM
from conciliador.modelos import Direcao, DocumentoExtraido, OrigemTexto, TipoDocumento

DOCUMENTOS = Path(__file__).resolve().parents[1] / "dados" / "exemplos" / "documentos"
EMPRESA = "52601815000120"
LINHAS = [
    "NOTA FISCAL DE SERVIÇOS ELETRÔNICA - NFS-e",
    "Número da NFS-e: 41",
    "Data de emissão: 16/10/2026",
    "PRESTADOR DE SERVIÇOS",
    "Razão social: Ateliê Ponto Firme Ltda",
    "CNPJ: 52.601.815/0001-20",
    "TOMADOR DE SERVIÇOS",
    "Razão social: Hotel Mar Azul Ltda",
    "CNPJ: 75.749.118/0001-16",
    "Vencimento: 15/11/2026",
    "VALOR TOTAL DA NOTA = R$ 1.150,00",
]


def pdf_com_texto(linhas: list[str]) -> bytes:
    saida = io.BytesIO()
    pdf = canvas.Canvas(saida, pagesize=A4)
    for indice, linha in enumerate(linhas):
        pdf.drawString(60, 780 - indice * 18, linha)
    pdf.save()
    return saida.getvalue()


def fonte(tamanho: int) -> ImageFont.ImageFont:
    try:
        return ImageFont.truetype("DejaVuSans.ttf", tamanho)
    except OSError:
        return ImageFont.load_default(tamanho)


def pagina_escaneada(linhas: list[str]) -> Image.Image:
    """Página A4 a 200 dpi com o texto desenhado como imagem e um pouco torta."""
    imagem = Image.new("L", (1654, 2339), 255)
    tela = ImageDraw.Draw(imagem)
    for indice, linha in enumerate(linhas):
        tela.text((130, 150 + indice * 60), linha, fill=0, font=fonte(30))
    return imagem.rotate(1.2, resample=Image.Resampling.BICUBIC, fillcolor=255)


def pdf_escaneado(linhas: list[str]) -> bytes:
    saida = io.BytesIO()
    pdf = canvas.Canvas(saida, pagesize=A4)
    pdf.drawImage(ImageReader(pagina_escaneada(linhas)), 0, 0, width=A4[0], height=A4[1])
    pdf.save()
    return saida.getvalue()


class ExtratorEspiao:
    """Guarda o que recebeu e devolve um documento fixo: isola a leitura do arquivo da extração de campos."""

    def __init__(self) -> None:
        self.chamadas: list[tuple[str, OrigemTexto]] = []

    def extrair(self, texto: str, origem: OrigemTexto) -> DocumentoExtraido:
        self.chamadas.append((texto, origem))
        return DocumentoExtraido(tipo=TipoDocumento.OUTRO, origem=origem, texto=texto, emitente_nome="Fulano de Tal",
                                 emitente_cnpj=EMPRESA, valor_total=Decimal("1150.00"), data_emissao=date(2026, 10, 1))


@pytest.mark.parametrize(
    ("conteudo", "nome", "formato"),
    [
        (b"%PDF-1.7\n...", "qualquer_nome.bin", Formato.PDF),
        (b"\x89PNG\r\n\x1a\n...", "foto.jpg", Formato.IMAGEM),  # assinatura manda mais que a extensão
        (b"\xff\xd8\xff\xe0...", "recibo", Formato.IMAGEM),
        (b"II*\x00...", "scan.tif", Formato.IMAGEM),
        (b"RIFF\x00\x00\x00\x00WEBPVP8 ", "foto.webp", Formato.IMAGEM),
        (b'\xef\xbb\xbf<?xml version="1.0"?><nfeProc xmlns="http://www.portalfiscal.inf.br/nfe"><infNFe/></nfeProc>',
         "nota.xml", Formato.XML_NFE),
        (b"conteudo sem assinatura", "foto.JPEG", Formato.IMAGEM),  # sem assinatura, a extensão decide
    ],
)
def test_detecta_formato(conteudo, nome, formato):
    assert detectar_formato(conteudo, nome) == formato


@pytest.mark.parametrize(
    ("conteudo", "nome", "mensagem"),
    [
        (b"<?xml version='1.0'?><CompNfse><Nfse/></CompNfse>", "nfse.xml", "não é NF-e"),
        (b"Data;Valor\n01/10/2026;10,00", "extrato.csv", "não reconhecido"),
    ],
)
def test_formato_nao_suportado(conteudo, nome, mensagem):
    with pytest.raises(FormatoNaoSuportado, match=mensagem):
        ler_documento(conteudo, nome)


def test_pdf_com_texto_vai_para_o_extrator_sem_ocr():
    espiao = ExtratorEspiao()
    ler_documento(pdf_com_texto(LINHAS), "nfse.pdf", extrator=espiao)
    texto, origem = espiao.chamadas[0]
    assert origem == OrigemTexto.PDF_TEXTO
    assert "VALOR TOTAL DA NOTA = R$ 1.150,00" in texto


def test_pdf_com_texto_lido_com_o_extrator_padrao(monkeypatch):
    monkeypatch.delenv("EXTRATOR", raising=False)
    doc = ler_documento(pdf_com_texto(LINHAS), "nfse.pdf", cnpj_empresa="52.601.815/0001-20")
    assert doc.tipo == TipoDocumento.NFSE
    assert doc.direcao == Direcao.RECEBER  # a empresa é a prestadora
    assert (doc.destinatario_nome, doc.destinatario_cnpj) == ("Hotel Mar Azul Ltda", "75749118000116")
    assert doc.numero == "41"
    assert (doc.data_emissao, doc.data_vencimento) == (date(2026, 10, 16), date(2026, 11, 15))
    assert doc.valor_total == Decimal("1150.00")
    assert doc.confianca == 1.0


def test_pdf_sem_camada_de_texto_cai_no_ocr():
    espiao = ExtratorEspiao()
    ler_documento(pdf_escaneado(LINHAS), "nfse_escaneada.pdf", extrator=espiao)
    texto, origem = espiao.chamadas[0]
    assert origem == OrigemTexto.OCR
    assert "1.150,00" in texto and "16/10/2026" in texto


def test_pdf_misto_so_faz_ocr_na_pagina_escaneada():
    saida = io.BytesIO()
    pdf = canvas.Canvas(saida, pagesize=A4)
    pdf.drawString(60, 780, "Página 1 com texto digital suficiente para não precisar de OCR nenhum.")
    pdf.showPage()
    pdf.drawImage(ImageReader(pagina_escaneada(LINHAS)), 0, 0, width=A4[0], height=A4[1])
    pdf.save()
    espiao = ExtratorEspiao()

    ler_documento(saida.getvalue(), "misto.pdf", extrator=espiao)

    texto, origem = espiao.chamadas[0]
    assert origem == OrigemTexto.OCR
    assert texto.startswith("Página 1 com texto digital")
    assert "1.150,00" in texto


def test_direcao_depende_do_cnpj_da_empresa():
    espiao = ExtratorEspiao()
    conteudo = pdf_com_texto(LINHAS)
    assert ler_documento(conteudo, "a.pdf", cnpj_empresa=EMPRESA, extrator=espiao).direcao == Direcao.RECEBER
    assert ler_documento(conteudo, "a.pdf", cnpj_empresa="13186091000143", extrator=espiao).direcao == Direcao.PAGAR
    assert ler_documento(conteudo, "a.pdf", extrator=espiao).direcao == Direcao.PAGAR


def test_xml_nao_passa_pelo_extrator(monkeypatch):
    # Extrator inválido no ambiente: se o XML tentasse usar extrator, quebraria.
    monkeypatch.setenv("EXTRATOR", "inexistente")
    arquivo = DOCUMENTOS / "nfe_1187_aviamentos_costa.xml"
    doc = ler_documento(arquivo.read_bytes(), arquivo.name, cnpj_empresa=EMPRESA)
    assert doc.origem == OrigemTexto.XML
    assert doc.valor_total == Decimal("486.50")


def test_confianca_soma_problema_do_llm_e_das_regras():
    # Duas respostas fora do schema (a segunda tem um campo a mais): sobra só o que valida campo a campo.
    respostas = ["isto não é JSON", json.dumps({"tipo": "nfse", "valor_total": "999.99", "inventado": True})]

    def provedor(pedido: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"choices": [{"message": {"content": respostas.pop(0)}}]})

    extrator = ExtratorLLM(cliente=httpx.Client(transport=httpx.MockTransport(provedor)), chave_api="x")

    doc = ler_documento(pdf_com_texto(LINHAS), "nfse.pdf", extrator=extrator)

    assert doc.valor_total == Decimal("999.99")  # aproveitado, mas denunciado por não estar escrito no texto
    assert [p.codigo for p in doc.problemas] == ["extracao_invalida", "valor_nao_encontrado_no_texto",
                                                 "data_emissao_ausente", "emitente_ausente"]
    assert doc.confianca == 0.0  # 0,4 + 0,3 + 0,2 + 0,2 passa de 1


@pytest.mark.parametrize(
    ("conteudo", "nome", "erro"),
    [(b"%PDF-1.4 quebrado", "x.pdf", PdfInvalido), (b"\x89PNG\r\n\x1a\nlixo", "x.png", ImagemInvalida)],
)
def test_arquivo_corrompido_da_erro_claro(conteudo, nome, erro):
    with pytest.raises(erro):
        ler_documento(conteudo, nome, extrator=ExtratorEspiao())
