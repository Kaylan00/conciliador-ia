import json
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from conciliador.extrato import ExtratoInvalido, ler_extrato

EXEMPLOS = Path(__file__).resolve().parents[1] / "dados" / "exemplos"


def _sgml(transacoes: str, charset: str = "1252") -> str:
    """OFX 1.02 como os bancos exportam: cabeçalho chave:valor e tags de campo sem fechamento."""
    return (
        "OFXHEADER:100\nDATA:OFXSGML\nVERSION:102\nSECURITY:NONE\nENCODING:USASCII\n"
        f"CHARSET:{charset}\nCOMPRESSION:NONE\nOLDFILEUID:NONE\nNEWFILEUID:NONE\n\n"
        "<OFX>\n<BANKMSGSRSV1>\n<STMTTRNRS>\n<STMTRS>\n<CURDEF>BRL\n"
        "<BANKTRANLIST>\n<DTSTART>20261001\n<DTEND>20261031\n"
        f"{transacoes}"
        "</BANKTRANLIST>\n</STMTRS>\n</STMTTRNRS>\n</BANKMSGSRSV1>\n</OFX>\n"
    )


def _stmttrn(
    fitid: str = "1", data: str = "20261012120000[-3:BRT]", valor: str = "-3480.00", memo: str = "PAG BOLETO"
) -> str:
    return f"<STMTTRN>\n<TRNTYPE>DEBIT\n<DTPOSTED>{data}\n<TRNAMT>{valor}\n<FITID>{fitid}\n<MEMO>{memo}\n</STMTTRN>\n"


def _ler(texto: str, encoding: str = "cp1252"):
    return ler_extrato(texto.encode(encoding), "extrato.ofx")


def test_le_o_ofx_de_exemplo_igual_ao_cenario():
    cenario = json.loads((EXEMPLOS / "cenario.json").read_text(encoding="utf-8"))
    brutos = [doc["pagamento"] for doc in cenario["documentos"] if doc["pagamento"]]
    brutos += cenario["transacoes_sem_documento"]
    esperado = sorted((date.fromisoformat(b["data"]), Decimal(b["valor"]), b["memo"], b["tipo"]) for b in brutos)

    transacoes = ler_extrato((EXEMPLOS / "extrato_outubro.ofx").read_bytes(), "extrato_outubro.ofx")

    assert [(t.data, t.valor, t.descricao, t.tipo) for t in transacoes] == esperado
    gabarito = json.loads((EXEMPLOS / "gabarito_conciliacao.json").read_text(encoding="utf-8"))
    assert [t.id for t in transacoes] == list(gabarito["transacoes"])


def test_um_lancamento_sgml_vira_transacao_completa():
    [transacao] = _ler(_sgml(_stmttrn(fitid="20261012001", memo="PAG BOLETO TECIDOS VALE VERDE LTDA")))

    assert transacao.id == "20261012001"
    assert transacao.data == date(2026, 10, 12)
    assert transacao.valor == Decimal("-3480.00")
    assert transacao.descricao == "PAG BOLETO TECIDOS VALE VERDE LTDA"
    assert transacao.tipo == "DEBIT"


@pytest.mark.parametrize(("charset", "encoding"), [("1252", "cp1252"), ("ISO-8859-1", "latin-1")])
def test_respeita_o_charset_do_cabecalho(charset, encoding):
    memo = "PIX ENVIADO JOSÉ CONCEIÇÃO AÇOUGUE"

    [transacao] = _ler(_sgml(_stmttrn(memo=memo), charset=charset), encoding=encoding)

    assert transacao.descricao == memo


@pytest.mark.parametrize(
    "texto",
    ["20261012", "20261012120000", "20261012120000[-3:BRT]", "20261012120000.000[-03:EST]", "20261012235959[0:GMT]"],
)
def test_data_com_fuso_sem_hora_ou_com_milissegundos(texto):
    [transacao] = _ler(_sgml(_stmttrn(data=texto)))

    assert transacao.data == date(2026, 10, 12)


@pytest.mark.parametrize(
    ("texto", "esperado"), [("-3480.00", "-3480.00"), ("-3480,00", "-3480.00"), ("+95.5", "95.50"), ("3.12", "3.12")]
)
def test_valor_com_ponto_ou_virgula_decimal(texto, esperado):
    [transacao] = _ler(_sgml(_stmttrn(valor=texto)))

    assert transacao.valor == Decimal(esperado)


def test_ignora_tags_desconhecidas_e_aceita_a_transacao_numa_linha_so():
    numa_linha = (
        "<STMTTRN><TRNTYPE>XFER<DTPOSTED>20261007<TRNAMT>-650.00<FITID>abc<CHECKNUM>318<REFNUM>99"
        "<PAYEE><NAME>CLIC CONTABILIDADE<CITY>VITORIA</PAYEE><BANCO.EXTRA>qualquer coisa"
        "<MEMO>PIX ENVIADO CLIC CONTABILIDADE LTDA</STMTTRN>"
    )

    [transacao] = _ler(_sgml(numa_linha))

    assert (transacao.id, transacao.valor, transacao.tipo) == ("abc", Decimal("-650.00"), "XFER")
    assert transacao.descricao == "PIX ENVIADO CLIC CONTABILIDADE LTDA"


def test_aceita_quebra_de_linha_do_windows():
    [transacao] = _ler(_sgml(_stmttrn(memo="TARIFA")).replace("\n", "\r\n"))

    assert transacao.descricao == "TARIFA"


def test_junta_name_e_memo_quando_o_banco_separa_favorecido_e_operacao():
    bloco = _stmttrn(memo="PIX ENVIADO").replace("<MEMO>", "<NAME>JOAO PEDRO S\n<MEMO>")

    [transacao] = _ler(_sgml(bloco))

    assert transacao.descricao == "PIX ENVIADO JOAO PEDRO S"


def test_ofx_2_em_xml_com_encoding_declarado_e_entidades():
    xml = """<?xml version="1.0" encoding="UTF-8" standalone="no"?>
<?OFX OFXHEADER="200" VERSION="211" SECURITY="NONE" OLDFILEUID="NONE" NEWFILEUID="NONE"?>
<OFX>
  <SIGNONMSGSRSV1><SONRS><STATUS><CODE>0</CODE><SEVERITY>INFO</SEVERITY></STATUS>
    <DTSERVER>20261031120000[-3:BRT]</DTSERVER><LANGUAGE>POR</LANGUAGE></SONRS></SIGNONMSGSRSV1>
  <BANKMSGSRSV1>
    <STMTTRNRS>
      <TRNUID>1</TRNUID>
      <STMTRS>
        <CURDEF>BRL</CURDEF>
        <BANKACCTFROM><BANKID>999</BANKID><ACCTID>12345-6</ACCTID><ACCTTYPE>CHECKING</ACCTTYPE></BANKACCTFROM>
        <BANKTRANLIST>
          <DTSTART>20261001</DTSTART><DTEND>20261031</DTEND>
          <STMTTRN>
            <TRNTYPE>XFER</TRNTYPE>
            <DTPOSTED>20261009100000[-3:BRT]</DTPOSTED>
            <TRNAMT>-486.50</TRNAMT>
            <FITID>X-1</FITID>
            <MEMO>PIX ENVIADO AVIAMENTOS COSTA &amp; FILHOS</MEMO>
          </STMTTRN>
          <STMTTRN>
            <TRNTYPE>CREDIT</TRNTYPE>
            <DTPOSTED>20261020</DTPOSTED>
            <TRNAMT>5200.00</TRNAMT>
            <FITID>X-2</FITID>
            <MEMO>PIX RECEBIDO LOJA BEM VESTIR - JOÃO</MEMO>
          </STMTTRN>
        </BANKTRANLIST>
        <LEDGERBAL><BALAMT>23163.50</BALAMT><DTASOF>20261031</DTASOF></LEDGERBAL>
      </STMTRS>
    </STMTTRNRS>
  </BANKMSGSRSV1>
</OFX>
"""
    transacoes = ler_extrato(xml.encode("utf-8"), "extrato.ofx")

    assert [(t.id, t.data, t.valor, t.tipo) for t in transacoes] == [
        ("X-1", date(2026, 10, 9), Decimal("-486.50"), "XFER"),
        ("X-2", date(2026, 10, 20), Decimal("5200.00"), "CREDIT"),
    ]
    assert transacoes[0].descricao == "PIX ENVIADO AVIAMENTOS COSTA & FILHOS"
    assert transacoes[1].descricao == "PIX RECEBIDO LOJA BEM VESTIR - JOÃO"


def test_sem_fitid_usa_hash_estavel_e_fitid_repetido_ganha_sufixo():
    sem_fitid = _stmttrn(memo="TARIFA").replace("<FITID>1\n", "")
    texto = _sgml(sem_fitid + _stmttrn(fitid="7", memo="A") + _stmttrn(fitid="7", memo="B"))

    primeira, segunda, terceira = _ler(texto)

    assert len(primeira.id) == 12
    assert primeira.id == _ler(texto)[0].id
    assert (segunda.id, terceira.id) == ("7", "7-2")


def test_extrato_sem_movimento_no_periodo_e_lista_vazia():
    assert _ler(_sgml("")) == []


@pytest.mark.parametrize(
    ("bloco", "mensagem"),
    [
        (_stmttrn().replace("<TRNAMT>-3480.00\n", ""), "sem valor (TRNAMT)"),
        (_stmttrn(valor="abc"), "valor 'abc' não é um número"),
        (_stmttrn().replace("<DTPOSTED>20261012120000[-3:BRT]\n", ""), "sem data (DTPOSTED)"),
        (_stmttrn(data="20261332"), "data '20261332' não está no formato AAAAMMDD"),
    ],
)
def test_transacao_quebrada_da_erro_dizendo_qual_e_o_que_falta(bloco, mensagem):
    with pytest.raises(ExtratoInvalido, match=r"extrato\.ofx: transação 1 \(FITID 1\)") as erro:
        _ler(_sgml(bloco))

    assert mensagem in str(erro.value)


def test_ofx_sem_lista_de_transacoes_da_erro():
    texto = _sgml("").replace("<BANKTRANLIST>\n<DTSTART>20261001\n<DTEND>20261031\n</BANKTRANLIST>\n", "")

    with pytest.raises(ExtratoInvalido, match="BANKTRANLIST"):
        _ler(texto)
