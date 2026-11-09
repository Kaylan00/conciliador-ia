from datetime import date
from decimal import Decimal

from conciliador.modelos import Direcao, DocumentoExtraido, Lancamento, OrigemTexto, Partida, TipoDocumento


def test_lancamento_fechado_quando_debito_igual_credito():
    l = Lancamento(
        data=date(2026, 10, 12),
        historico="Pagamento NF 4521",
        partidas=[Partida(conta="2.1.1", debito=Decimal("3480.00")), Partida(conta="1.1.1", credito=Decimal("3480.00"))],
    )
    assert l.fechado


def test_lancamento_aberto_quando_partidas_nao_batem():
    l = Lancamento(
        data=date(2026, 10, 12),
        historico="x",
        partidas=[Partida(conta="2.1.1", debito=Decimal("10")), Partida(conta="1.1.1", credito=Decimal("9.99"))],
    )
    assert not l.fechado


def test_contraparte_depende_da_direcao():
    venda = DocumentoExtraido(
        tipo=TipoDocumento.NFE, direcao=Direcao.RECEBER, origem=OrigemTexto.XML,
        emitente_nome="Ateliê", destinatario_nome="Loja Bem Vestir Ltda", destinatario_cnpj="43231948000160",
    )
    assert venda.contraparte_nome == "Loja Bem Vestir Ltda"
    assert venda.contraparte_cnpj == "43231948000160"
