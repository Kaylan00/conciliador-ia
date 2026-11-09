from datetime import date
from decimal import Decimal

import pytest

from conciliador.extracao.regras import (
    DESTINATARIO,
    EMITENTE,
    ExtratorRegras,
    encontrar_cnpjs,
    encontrar_datas,
    extrair_numero,
    extrair_tipo,
    extrair_valor_total,
)
from conciliador.modelos import OrigemTexto, TipoDocumento

NFSE = """PREFEITURA MUNICIPAL DE VITÓRIA
Secretaria Municipal da Fazenda
NOTA FISCAL DE SERVIÇOS ELETRÔNICA - NFS-e
Número da NFS-e
000318
Data e hora de emissão
05/10/2026 15:19:18
PRESTADOR DE SERVIÇOS
Razão social: Clic Contabilidade Ltda
CNPJ: 13.186.091/0001-43
Inscrição municipal: 0458123
TOMADOR DE SERVIÇOS
Razão social: Ateliê Ponto Firme Ltda
CNPJ: 52.601.815/0001-20
DISCRIMINAÇÃO DOS SERVIÇOS
Honorários contábeis - outubro/2026
Valor dos serviços
Base de cálculo
Valor do ISS
R$ 650,00
R$ 650,00
R$ 13,00
VALOR TOTAL DA NOTA = R$ 650,00
Vencimento: 10/10/2026. Pagamento por boleto bancário ou PIX.
"""

FATURA = """Luz Capixaba Energia S.A.
CNPJ 39.099.603/0001-28 Inscrição estadual 081234567
CONTA DE ENERGIA ELÉTRICA
Fatura nº 2026100045871
Data de emissão: 01/10/2026
Cliente: Ateliê Ponto Firme Ltda
CNPJ: 52.601.815/0001-20
Leitura anterior: 31/08/2026 Leitura atual: 30/09/2026 Consumo: 412 kWh
Consumo de energia elétrica - 412 kWh 360,50
Contribuição de iluminação pública - CIP 44,10
Vencimento: 15/10/2026
Total a pagar: R$ 412,37
Linha digitável
83660000004-1 12370138000-2 00092026100-4 04587100000-7
"""

# Recibo como o OCR devolve de uma foto ruim: título perdido, "CNP)", O no lugar de 0, espaço no número.
RECIBO_OCR = """ira eta me ires nte dra
Recebi de Ateliê Ponto Firme Ltda, CNP) 52.6O1.815/OOO1-2O, a
importância de RS 1 .800, 00 (mil e oitocentos reais), referente a aluguel

da sala comercial - outubro/2026.

Vitória, 5 de outubro de 2026.

| José Carlos Almeida
"""


def extrair(texto: str, origem: OrigemTexto = OrigemTexto.PDF_TEXTO):
    return ExtratorRegras().extrair(texto, origem)


def test_nfse_completa():
    doc = extrair(NFSE)
    assert doc.tipo == TipoDocumento.NFSE
    assert (doc.emitente_nome, doc.emitente_cnpj) == ("Clic Contabilidade Ltda", "13186091000143")
    assert (doc.destinatario_nome, doc.destinatario_cnpj) == ("Ateliê Ponto Firme Ltda", "52601815000120")
    assert doc.numero == "318"  # zeros à esquerda não fazem parte do número
    assert doc.data_emissao == date(2026, 10, 5)
    assert doc.data_vencimento == date(2026, 10, 10)
    assert doc.valor_total == Decimal("650.00")  # "valor total" ganha do ISS e da base de cálculo
    assert doc.descricao == "Honorários contábeis - outubro/2026"
    assert doc.origem == OrigemTexto.PDF_TEXTO
    assert doc.texto == NFSE


def test_fatura_ignora_datas_de_leitura_e_linha_digitavel():
    doc = extrair(FATURA)
    assert doc.tipo == TipoDocumento.FATURA
    assert (doc.emitente_nome, doc.emitente_cnpj) == ("Luz Capixaba Energia S.A.", "39099603000128")
    assert doc.destinatario_cnpj == "52601815000120"
    assert doc.numero == "2026100045871"
    assert doc.data_emissao == date(2026, 10, 1)
    assert doc.data_vencimento == date(2026, 10, 15)
    assert doc.valor_total == Decimal("412.37")


def test_recibo_com_sujeira_de_ocr():
    doc = extrair(RECIBO_OCR, OrigemTexto.OCR)
    assert doc.tipo == TipoDocumento.RECIBO  # pela frase "Recebi de ... a importância de", sem o título
    assert doc.valor_total == Decimal("1800.00")
    assert doc.data_emissao == date(2026, 10, 5)
    assert doc.data_vencimento == date(2026, 10, 5)  # recibo é pago na hora
    assert doc.emitente_cnpj is None  # o único CNPJ é de quem pagou
    assert doc.emitente_nome == "José Carlos Almeida"
    assert (doc.destinatario_nome, doc.destinatario_cnpj) == ("Ateliê Ponto Firme Ltda", "52601815000120")
    assert doc.descricao == "Aluguel da sala comercial - outubro/2026"
    assert doc.numero is None


def test_recibo_de_empresa_com_cnpj_de_quem_assina():
    texto = ("Nº 771 RECIBO R$ 95,00\nRecebemos de Ateliê Ponto Firme Ltda, CNPJ 52.601.815/0001-20, a importância "
             "de R$ 95,00 (noventa e cinco reais), referente a motofrete - entregas a clientes.\n"
             "Vitória, 14/10/2026.\nLeva e Traz Entregas Ltda\nCNPJ 51.819.093/0001-11")
    doc = extrair(texto, OrigemTexto.OCR)
    assert (doc.emitente_nome, doc.emitente_cnpj) == ("Leva e Traz Entregas Ltda", "51819093000111")
    assert doc.destinatario_cnpj == "52601815000120"
    assert doc.numero == "771"
    assert doc.data_emissao == doc.data_vencimento == date(2026, 10, 14)


@pytest.mark.parametrize(
    ("texto", "tipo"),
    [
        ("NOTA FISCAL DE SERVIÇOS ELETRÔNICA - NFS-e\nPagamento por boleto", TipoDocumento.NFSE),
        ("Nota Fiscal de Serviço\nValor", TipoDocumento.NFSE),
        ("FATURA DE SERVIÇOS DE INTERNET", TipoDocumento.FATURA),
        ("Conta de energia elétrica", TipoDocumento.FATURA),
        ("RECIBO\nR$ 10,00", TipoDocumento.RECIBO),
        ("DANFE - Documento auxiliar da nota fiscal eletrônica", TipoDocumento.NFE),
        ("Ficha de compensação\nLinha digitável", TipoDocumento.BOLETO),
        ("Lista de compras", TipoDocumento.OUTRO),
    ],
)
def test_tipo_pela_palavra_chave(texto, tipo):
    assert extrair_tipo(texto) == tipo


@pytest.mark.parametrize(
    ("texto", "valor"),
    [
        ("Valor do ISS R$ 13,00\nVALOR TOTAL DA NOTA = R$ 650,00", "650.00"),
        ("Valor total dos tributos: R$ 13,00\nTotal: R$ 650,00", "650.00"),  # rótulo seguido de outra palavra não vale
        ("Valor a pagar\nR$ 1.234,56", "1234.56"),  # valor na linha de baixo do rótulo
        ("a importância de R$1.800,00 (mil e oitocentos reais)", "1800.00"),
        ("Alíquota 2,00%\nR$ 15,00 e R$ 129,90", "129.90"),  # sem rótulo: maior valor com R$; % não é dinheiro
        ("sem valor nenhum aqui", None),
    ],
)
def test_valor_total_prioriza_rotulo(texto, valor):
    assert extrair_valor_total(texto) == (Decimal(valor) if valor else None)


def test_cnpj_rotulado_entra_mesmo_com_dv_errado_e_solto_so_com_dv_certo():
    texto = "CNPJ: 13.186.091/0001-44\nCódigo 11222333000180\nCódigo 11222333000181"
    assert [c.digitos for c in encontrar_cnpjs(texto)] == ["13186091000144", "11222333000181"]


def test_papel_do_cnpj_vem_do_rotulo_mais_proximo():
    cnpjs = encontrar_cnpjs(NFSE)
    assert [(c.digitos, c.papel) for c in cnpjs] == [("13186091000143", EMITENTE), ("52601815000120", DESTINATARIO)]


def test_datas_por_rotulo_intervalo_e_extenso():
    texto = "Período: 01/10/2026 a 31/10/2026\nEmitida em 1º de novembro de 2026\nPagável até 10/11/2026"
    datas = encontrar_datas(texto)
    assert [(d.valor, d.rotulo) for d in datas] == [
        (date(2026, 10, 1), "outra"),
        (date(2026, 10, 31), "outra"),  # "a 31/10" herda o rótulo do começo do intervalo
        (date(2026, 11, 1), "emissao"),
        (date(2026, 11, 10), "vencimento"),
    ]


def test_data_impossivel_e_ignorada():
    assert encontrar_datas("Emissão: 31/02/2026") == []


def test_dia_por_extenso_com_letra_no_lugar_do_numero():
    assert [d.valor for d in encontrar_datas("Vitória, S de outubro de 2026.")] == [date(2026, 10, 5)]
    assert encontrar_datas("pago de outubro de 2026") == []  # "o" dentro de palavra não vira dia


def test_recibo_sem_data_legivel_ainda_acha_quem_assina():
    texto = ("Recebi de Ateliê Ponto Firme Ltda, CNPJ 52.601.815/0001-20, a importância de R$ 1.800,00 "
             "(mil e oitocentos reais).\nVitória, ?? de outubro.\nJosé Carlos Almeida")
    doc = extrair(texto, OrigemTexto.OCR)
    assert doc.emitente_nome == "José Carlos Almeida"  # e não o nome de quem pagou, que vem depois de "Recebi de"


@pytest.mark.parametrize(
    ("texto", "numero"),
    [
        ("Fatura nº 88213", "88213"),
        ("RECIBO Nº 771", "771"),
        ("Número da NFS-e\n000318", "318"),
        ("Nº 41\nRECIBO", "41"),
        ("Ne 771 RECIBO", "771"),  # º lido como e
        ("Inscrição municipal: 0458123", None),
    ],
)
def test_numero_do_documento(texto, numero):
    assert extrair_numero(texto) == numero


def test_nome_do_emitente_quando_o_ocr_junta_colunas():
    texto = "Rede Sul Fibra Ltda FATURA DE SERVIÇOS DE INTERNET\nCNPJ 08.246.281/0001-79 Fatura nº 88213"
    assert extrair(texto, OrigemTexto.OCR).emitente_nome == "Rede Sul Fibra Ltda"


def test_texto_vazio_nao_inventa_nada():
    doc = extrair("", OrigemTexto.OCR)
    assert doc.tipo == TipoDocumento.OUTRO
    vazios = ("emitente_nome", "emitente_cnpj", "destinatario_nome", "destinatario_cnpj", "numero", "data_emissao",
              "data_vencimento", "valor_total", "descricao")
    assert all(getattr(doc, campo) is None for campo in vazios)
