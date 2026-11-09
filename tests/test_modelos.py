from datetime import date
from decimal import Decimal

from conciliador.modelos import Direcao, DocumentoExtraido, Lancamento, OrigemTexto, Partida, TipoDocumento


def _lancamento(debito: str, credito: str) -> Lancamento:
    return Lancamento(
        data=date(2026, 10, 12),
        historico="Pagamento NF 4521",
        partidas=[
            Partida(conta="2.1.1", debito=Decimal(debito)),
            Partida(conta="1.1.1", credito=Decimal(credito)),
        ],
    )


def test_lancamento_fechado_quando_debito_igual_credito():
    assert _lancamento("3480.00", "3480.00").fechado


def test_lancamento_aberto_quando_partidas_nao_batem():
    assert not _lancamento("10", "9.99").fechado


def test_contraparte_depende_da_direcao():
    venda = DocumentoExtraido(
        tipo=TipoDocumento.NFE,
        direcao=Direcao.RECEBER,
        origem=OrigemTexto.XML,
        emitente_nome="Ateliê",
        destinatario_nome="Loja Bem Vestir Ltda",
        destinatario_cnpj="43231948000160",
    )
    assert venda.contraparte_nome == "Loja Bem Vestir Ltda"
    assert venda.contraparte_cnpj == "43231948000160"
