from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from conciliador.documentos import ler_documento
from conciliador.documentos.nfe_xml import NFeInvalida, ler_nfe
from conciliador.modelos import Direcao, OrigemTexto, TipoDocumento

DOCUMENTOS = Path(__file__).resolve().parents[1] / "dados" / "exemplos" / "documentos"
EMPRESA = "52601815000120"


def nfe_minima(*, ide: str = "<nNF>7</nNF><dhEmi>2026-10-31T23:30:00-03:00</dhEmi>", itens: str = "",
               vnf: str = "100.00", cobr: str = "") -> bytes:
    """NF-e sem o envelope nfeProc, só com o que o leitor usa."""
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<NFe xmlns="http://www.portalfiscal.inf.br/nfe"><infNFe versao="4.00" Id="NFe1">
<ide>{ide}</ide>
<emit><CNPJ>13186091000143</CNPJ><xNome>Clic Contabilidade Ltda</xNome></emit>
{itens}
<total><ICMSTot><vNF>{vnf}</vNF></ICMSTot></total>{cobr}
</infNFe></NFe>""".encode()


def test_le_a_nfe_de_exemplo_com_itens_e_duplicata():
    doc = ler_nfe((DOCUMENTOS / "nfe_4521_tecidos_vale_verde.xml").read_bytes())

    assert doc.tipo == TipoDocumento.NFE
    assert doc.origem == OrigemTexto.XML and doc.texto == ""
    assert (doc.emitente_nome, doc.emitente_cnpj) == ("Tecidos Vale Verde Ltda", "90830166000128")
    assert (doc.destinatario_nome, doc.destinatario_cnpj) == ("Ateliê Ponto Firme Ltda", EMPRESA)
    assert doc.numero == "4521"
    assert doc.data_emissao == date(2026, 10, 2)
    assert doc.data_vencimento == date(2026, 10, 12)
    assert doc.valor_total == Decimal("3480.00")
    assert [(i.descricao, i.quantidade, i.valor_unitario, i.valor_total) for i in doc.itens] == [
        ("Tecido linho cru 1,40 m", Decimal("60"), Decimal("38.00"), Decimal("2280.00")),
        ("Tricoline estampada 1,50 m", Decimal("50"), Decimal("24.00"), Decimal("1200.00")),
    ]
    assert str(doc.itens[0].quantidade) == "60" and str(doc.itens[0].valor_unitario) == "38.00"
    assert doc.descricao == "Tecido linho cru 1,40 m; Tricoline estampada 1,50 m"


def test_venda_da_propria_empresa_vira_receber():
    doc = ler_documento((DOCUMENTOS / "nfe_0093_venda_loja_bem_vestir.xml").read_bytes(),
                        "nfe_0093_venda_loja_bem_vestir.xml", cnpj_empresa="52.601.815/0001-20")
    assert doc.direcao == Direcao.RECEBER
    assert doc.contraparte_nome == "Loja Bem Vestir Ltda"
    assert doc.confianca == 1.0


def test_nfe_sem_envelope_e_data_no_fuso_de_quem_emitiu():
    doc = ler_nfe(nfe_minima())
    assert doc.numero == "7"
    assert doc.data_emissao == date(2026, 10, 31)  # 23h30 em Brasília, mesmo sendo 1º/11 em UTC
    assert doc.destinatario_cnpj is None and doc.data_vencimento is None and doc.itens == []


def test_leiaute_antigo_com_dEmi_e_varias_parcelas():
    cobr = ("<cobr><dup><nDup>2</nDup><dVenc>2026-12-10</dVenc><vDup>50.00</vDup></dup>"
            "<dup><nDup>1</nDup><dVenc>2026-11-10</dVenc><vDup>50.00</vDup></dup></cobr>")
    doc = ler_nfe(nfe_minima(ide="<nNF>8</nNF><dEmi>2026-10-15</dEmi>", cobr=cobr))
    assert doc.data_emissao == date(2026, 10, 15)
    assert doc.data_vencimento == date(2026, 11, 10)  # a primeira parcela a vencer


def test_preco_unitario_com_mais_casas_nao_perde_precisao():
    itens = ("<det nItem='1'><prod><xProd>Botão</xProd><qCom>1000.0000</qCom><vUnCom>0.1234000000</vUnCom>"
             "<vProd>123.40</vProd></prod></det>")
    item = ler_nfe(nfe_minima(itens=itens, vnf="123.40")).itens[0]
    assert item.quantidade == Decimal("1000")
    assert item.valor_unitario == Decimal("0.1234")


def test_recusa_doctype_para_evitar_xxe_e_expansao_de_entidades():
    malicioso = (b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY e SYSTEM "file:///etc/passwd">]>'
                 b'<NFe xmlns="http://www.portalfiscal.inf.br/nfe"><infNFe>&e;</infNFe></NFe>')
    with pytest.raises(NFeInvalida, match="DOCTYPE"):
        ler_nfe(malicioso)


@pytest.mark.parametrize(
    ("conteudo", "mensagem"),
    [
        (b"<NFe xmlns='http://www.portalfiscal.inf.br/nfe'><infNFe>", "mal formado"),
        (b"<nfeProc xmlns='http://www.portalfiscal.inf.br/nfe'><outra/></nfeProc>", "infNFe"),
        (nfe_minima(vnf="cem reais"), "vNF"),
    ],
)
def test_xml_que_nao_da_para_ler(conteudo, mensagem):
    with pytest.raises(NFeInvalida, match=mensagem):
        ler_nfe(conteudo)
