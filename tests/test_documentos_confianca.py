from datetime import date
from decimal import Decimal

import pytest

from conciliador.documentos.confianca import PENALIDADES, aplicar_confianca
from conciliador.modelos import DocumentoExtraido, ItemDocumento, OrigemTexto, Problema, TipoDocumento

TEXTO = "Clic Contabilidade Ltda CNPJ 13.186.091/0001-43 Emissão 05/10/2026 VALOR TOTAL R$ 1.650,00"


def documento(**mudancas) -> DocumentoExtraido:
    campos = {
        "tipo": TipoDocumento.NFSE,
        "emitente_nome": "Clic Contabilidade Ltda",
        "emitente_cnpj": "13186091000143",
        "destinatario_cnpj": "52601815000120",
        "data_emissao": date(2026, 10, 5),
        "valor_total": Decimal("1650.00"),
        "origem": OrigemTexto.PDF_TEXTO,
        "texto": TEXTO,
    }
    return DocumentoExtraido(**{**campos, **mudancas})


def codigos(doc: DocumentoExtraido) -> list[str]:
    return [p.codigo for p in doc.problemas]


def test_documento_sem_problema_tem_confianca_total():
    doc = aplicar_confianca(documento(), confianca_ocr=91.5)
    assert doc.problemas == []
    assert doc.confianca == 1.0


@pytest.mark.parametrize(
    ("mudancas", "codigo"),
    [
        ({"emitente_cnpj": "13186091000144"}, "cnpj_invalido"),
        ({"destinatario_cnpj": "5260181500012"}, "cnpj_invalido"),
        ({"valor_total": None}, "valor_ausente"),
        ({"valor_total": Decimal("1560.00")}, "valor_nao_encontrado_no_texto"),
        ({"itens": [ItemDocumento(descricao="Honorários", valor_unitario=Decimal("650"), valor_total=Decimal("650"))]},
         "total_diferente_dos_itens"),
        ({"data_emissao": None}, "data_emissao_ausente"),
        ({"emitente_nome": None, "emitente_cnpj": None}, "emitente_ausente"),
        ({"emitente_cnpj": None}, "cnpj_emitente_ausente"),
    ],
)
def test_cada_regra_vira_um_problema_com_a_penalidade_dela(mudancas, codigo):
    doc = aplicar_confianca(documento(**mudancas))
    assert codigos(doc) == [codigo]
    assert doc.problemas[0].penalidade == PENALIDADES[codigo]
    assert doc.confianca == pytest.approx(1 - PENALIDADES[codigo])
    assert doc.problemas[0].mensagem  # frase para a pessoa da revisão, não só o código


def test_mensagem_do_cnpj_mostra_o_numero_formatado():
    doc = aplicar_confianca(documento(emitente_cnpj="13186091000144"))
    assert "13.186.091/0001-44" in doc.problemas[0].mensagem


def test_ocr_com_confianca_baixa():
    doc = aplicar_confianca(documento(origem=OrigemTexto.OCR), confianca_ocr=55.0)
    assert codigos(doc) == ["ocr_baixa_confianca"]
    assert "55%" in doc.problemas[0].mensagem


def test_valor_que_nao_bate_com_o_extenso():
    # O OCR leu 95 como 93 nos algarismos, mas o extenso continua dizendo noventa e cinco.
    texto = "RECIBO R$ 93,00\nRecebemos a importância de R$ 93,00 (noventa e cinco reais)"
    doc = aplicar_confianca(documento(tipo=TipoDocumento.RECIBO, valor_total=Decimal("93.00"), texto=texto))
    assert codigos(doc) == ["valor_diferente_do_extenso"]
    assert "R$ 95,00" in doc.problemas[0].mensagem


def test_valor_que_bate_com_o_extenso_nao_reclama():
    texto = "a importância de R$ 1.650,00 (mil seiscentos e cinquenta reais)"
    assert aplicar_confianca(documento(texto=texto)).problemas == []


def test_recibo_pode_nao_ter_cnpj_do_emitente():
    doc = aplicar_confianca(documento(tipo=TipoDocumento.RECIBO, emitente_nome="José Carlos Almeida",
                                      emitente_cnpj=None))
    assert doc.problemas == []


def test_ocr_ruim_sozinho_manda_para_revisao():
    assert aplicar_confianca(documento(origem=OrigemTexto.OCR), confianca_ocr=60.0).confianca < 0.8


def test_valor_sem_separador_de_milhar_conta_como_encontrado():
    doc = aplicar_confianca(documento(texto="TOTAL 1650,00"))
    assert "valor_nao_encontrado_no_texto" not in codigos(doc)


def test_xml_nao_tem_texto_e_nao_e_cobrado_por_isso():
    doc = aplicar_confianca(documento(origem=OrigemTexto.XML, texto=""))
    assert doc.problemas == []


def test_itens_que_somam_o_total_nao_reclamam():
    itens = [ItemDocumento(descricao=f"item {n}", quantidade=Decimal("3"), valor_unitario=Decimal("183.33"),
                           valor_total=Decimal("550.00")) for n in range(3)]
    assert aplicar_confianca(documento(itens=itens)).problemas == []


def test_problemas_do_extrator_entram_na_soma_e_a_confianca_nao_fica_negativa():
    do_modelo = Problema(codigo="extracao_invalida", mensagem="JSON inválido duas vezes", penalidade=0.4)
    doc = aplicar_confianca(
        documento(problemas=[do_modelo], emitente_cnpj="13186091000144", valor_total=None, data_emissao=None),
        confianca_ocr=40.0,
    )
    assert codigos(doc) == ["extracao_invalida", "cnpj_invalido", "valor_ausente", "data_emissao_ausente",
                            "ocr_baixa_confianca"]
    assert doc.confianca == 0.0


def test_duas_penalidades_somam():
    doc = aplicar_confianca(documento(data_emissao=None, emitente_cnpj="13186091000144"))
    assert doc.confianca == pytest.approx(1 - PENALIDADES["data_emissao_ausente"] - PENALIDADES["cnpj_invalido"])
