from datetime import date
from decimal import Decimal

import pytest

from conciliador.conciliacao import candidatos, conciliar
from conciliador.modelos import Direcao, DocumentoExtraido, OrigemTexto, TipoDocumento, TransacaoBancaria


def _documento(
    valor: str | None = "3480.00",
    *,
    direcao: Direcao = Direcao.PAGAR,
    nome: str = "Tecidos Vale Verde Ltda",
    cnpj: str | None = "90830166000128",
    emissao: date | None = date(2026, 10, 2),
    vencimento: date | None = date(2026, 10, 12),
) -> DocumentoExtraido:
    lado = "emitente" if direcao == Direcao.PAGAR else "destinatario"
    return DocumentoExtraido(
        tipo=TipoDocumento.NFE,
        direcao=direcao,
        origem=OrigemTexto.XML,
        data_emissao=emissao,
        data_vencimento=vencimento,
        valor_total=Decimal(valor) if valor else None,
        **{f"{lado}_nome": nome, f"{lado}_cnpj": cnpj},
    )


def _transacao(
    id_: str = "t1",
    valor: str = "-3480.00",
    dia: date = date(2026, 10, 12),
    memo: str = "PAG BOLETO TECIDOS VALE VERDE",
) -> TransacaoBancaria:
    return TransacaoBancaria(id=id_, data=dia, valor=Decimal(valor), descricao=memo)


def _pares(resultado) -> dict[str, str]:
    return {par.transacao_id: par.documento_id for par in resultado.pares}


def test_casamento_simples_com_motivos_legiveis():
    resultado = conciliar({"nf-4521": _documento()}, [_transacao()])

    [par] = resultado.pares
    assert (par.transacao_id, par.documento_id, par.score) == ("t1", "nf-4521", 1.0)
    assert par.motivos == ["valor igual (R$ 3.480,00)", "pago no dia do vencimento", "nome parecido com o memo (100%)"]
    assert resultado.transacoes_sem_documento == []
    assert resultado.documentos_sem_transacao == []


@pytest.mark.parametrize(
    ("direcao", "valor", "casa"),
    [
        (Direcao.PAGAR, "-3480.00", True),
        (Direcao.PAGAR, "3480.00", False),
        (Direcao.RECEBER, "3480.00", True),
        (Direcao.RECEBER, "-3480.00", False),
    ],
)
def test_pagar_so_casa_com_saida_e_receber_so_com_entrada(direcao, valor, casa):
    resultado = conciliar({"d": _documento(direcao=direcao)}, [_transacao(valor=valor)])

    assert bool(resultado.pares) is casa


def test_recebimento_explica_com_o_verbo_certo():
    venda = _documento("5200.00", direcao=Direcao.RECEBER, nome="Loja Bem Vestir Ltda", vencimento=date(2026, 10, 20))

    pix = _transacao(valor="5200.00", dia=date(2026, 10, 20), memo="PIX RECEBIDO LOJA BEM VESTIR")

    [par] = conciliar({"venda": venda}, [pix]).pares

    assert "recebido no dia do vencimento" in par.motivos


@pytest.mark.parametrize(("valor", "casa"), [("-3480.01", True), ("-3479.99", True), ("-3480.02", False)])
def test_tolerancia_de_um_centavo(valor, casa):
    resultado = conciliar({"d": _documento()}, [_transacao(valor=valor)])

    assert bool(resultado.pares) is casa
    if casa:
        assert resultado.pares[0].motivos[0].startswith("valor bate com diferença de R$ 0,01")


def test_documento_sem_valor_nao_casa():
    resultado = conciliar({"d": _documento(valor=None)}, [_transacao()])

    assert resultado.pares == []
    assert resultado.documentos_sem_transacao == ["d"]
    assert resultado.transacoes_sem_documento == ["t1"]


def test_score_abaixo_do_minimo_vira_sobra_mas_continua_como_alternativa():
    honorarios = _documento(
        "650.00", nome="Clic Contabilidade Ltda", emissao=date(2026, 10, 5), vencimento=date(2026, 10, 10)
    )
    pix_para_outra_pessoa = _transacao(valor="-650.00", dia=date(2026, 10, 21), memo="PIX ENVIADO JOAO PEDRO S")

    resultado = conciliar({"nfse-318": honorarios}, [pix_para_outra_pessoa])

    assert resultado.pares == []
    assert resultado.documentos_sem_transacao == ["nfse-318"]
    assert resultado.transacoes_sem_documento == ["t1"]
    [alternativa] = candidatos(honorarios, [pix_para_outra_pessoa])
    assert alternativa.score < 0.55
    assert alternativa.motivos[1] == "pago 11 dias depois do vencimento"
    assert alternativa.motivos[2].startswith("nome diferente do memo")


def test_score_minimo_e_ajustavel():
    documento = _documento(vencimento=date(2026, 10, 1), emissao=date(2026, 9, 25))  # pago 11 dias depois
    transacao = _transacao(memo="PIX ENVIADO JOAO PEDRO S")

    assert conciliar({"d": documento}, [transacao]).pares == []
    assert len(conciliar({"d": documento}, [transacao], score_minimo=0.2).pares) == 1


@pytest.mark.parametrize(
    ("dia", "motivo"),
    [
        (date(2026, 10, 12), "pago no dia do vencimento"),
        (date(2026, 10, 11), "pago 1 dia antes do vencimento"),
        (date(2026, 10, 17), "pago 5 dias depois do vencimento"),
        (date(2026, 10, 27), "pago 15 dias depois do vencimento"),
    ],
)
def test_data_explica_a_distancia_ate_o_vencimento(dia, motivo):
    [candidato] = candidatos(_documento(), [_transacao(dia=dia)])

    assert candidato.motivos[1] == motivo


def test_score_cai_com_a_distancia_ate_o_vencimento():
    dias = [date(2026, 10, 12), date(2026, 10, 17), date(2026, 10, 27), date(2026, 11, 5)]

    scores = [candidatos(_documento(), [_transacao(dia=dia)])[0].score for dia in dias]

    assert scores == sorted(scores, reverse=True)
    assert scores[0] == 1.0
    assert scores[-1] == pytest.approx(0.5)  # 24 dias depois: a data não soma mais nada, sobra só o nome


def test_sem_vencimento_mede_a_partir_da_emissao():
    [candidato] = candidatos(_documento(vencimento=None), [_transacao(dia=date(2026, 10, 4))])

    assert candidato.motivos[1] == "pago 2 dias depois da emissão"


def test_pagamento_antes_da_emissao_penaliza_forte():
    reposicao = _documento(emissao=date(2026, 10, 20), vencimento=date(2026, 10, 30))

    [candidato] = candidatos(reposicao, [_transacao(dia=date(2026, 10, 12))])

    assert candidato.motivos[1] == "pago 8 dias antes da emissão do documento"
    assert candidato.score == pytest.approx(0.25)
    assert conciliar({"d": reposicao}, [_transacao(dia=date(2026, 10, 12))]).pares == []


def test_cnpj_da_contraparte_no_memo_da_bonus():
    sem_cnpj = _transacao(dia=date(2026, 10, 24), memo="PIX ENVIADO TECIDOS VALE VERDE")
    com_cnpj = _transacao(dia=date(2026, 10, 24), memo="PIX ENVIADO TECIDOS VALE VERDE 90.830.166/0001-28")

    [sem] = candidatos(_documento(), [sem_cnpj])
    [com] = candidatos(_documento(), [com_cnpj])

    assert com.score == pytest.approx(sem.score + 0.2)
    assert com.motivos[-1] == "CNPJ da contraparte aparece no memo"
    assert "CNPJ da contraparte aparece no memo" not in sem.motivos


def test_memo_sem_nome_nao_confirma_nem_desmente():
    [candidato] = candidatos(_documento(), [_transacao(memo="PAGAMENTO DE TITULO 0001234")])

    assert candidato.motivos[2] == "memo sem nome para comparar"
    assert candidato.score == pytest.approx(0.75)


def test_atribuicao_global_nao_gulosa():
    """O melhor par isolado (boleto 1 com o pagamento do dia 11) deixaria o boleto 2 sem nada.

    O boleto 2 só foi emitido no dia 11, então o pagamento do dia 5 não pode ser dele; o do dia 11 pode ser dos dois.
    Olhando o conjunto, o boleto 1 fica com o pagamento antecipado do dia 5 e o boleto 2 com o do dia 11.
    """
    boleto_1 = _documento(
        "500.00", nome="Nuvem Gestão Sistemas", emissao=date(2026, 10, 1), vencimento=date(2026, 10, 10)
    )
    boleto_2 = _documento(
        "500.00", nome="Nuvem Gestão Sistemas", emissao=date(2026, 10, 11), vencimento=date(2026, 10, 13)
    )
    dia_11 = _transacao("dia-11", "-500.00", date(2026, 10, 11), "PAG BOLETO NUVEM GESTAO SISTEMAS")
    dia_5 = _transacao("dia-05", "-500.00", date(2026, 10, 5), "PAG BOLETO NUVEM GESTAO SISTEMAS")
    assert candidatos(boleto_1, [dia_11, dia_5])[0].transacao.id == "dia-11"  # o que um guloso escolheria

    resultado = conciliar({"boleto-1": boleto_1, "boleto-2": boleto_2}, [dia_11, dia_5])

    assert _pares(resultado) == {"dia-05": "boleto-1", "dia-11": "boleto-2"}
    assert resultado.transacoes_sem_documento == resultado.documentos_sem_transacao == []


def test_candidatos_lista_as_alternativas_da_mais_para_a_menos_provavel():
    transacoes = [
        _transacao("t-30", dia=date(2026, 10, 30)),
        _transacao("t-12", dia=date(2026, 10, 12)),
        _transacao("estorno", valor="3480.00", dia=date(2026, 10, 12)),
        _transacao("outro-valor", valor="-348.00", dia=date(2026, 10, 12)),
    ]

    lista = candidatos(_documento(), transacoes)

    assert [c.transacao.id for c in lista] == ["t-12", "t-30"]
    assert lista[0].score > lista[1].score


def test_sem_documentos_ou_sem_transacoes_tudo_sobra():
    assert conciliar({}, [_transacao()]).transacoes_sem_documento == ["t1"]
    assert conciliar({"d": _documento()}, []).documentos_sem_transacao == ["d"]


def test_pares_saem_na_ordem_do_extrato():
    documentos = {
        "b": _documento("10.00", nome="Fornecedor B", vencimento=date(2026, 10, 20)),
        "a": _documento("20.00", nome="Fornecedor A", vencimento=date(2026, 10, 5)),
    }
    transacoes = [
        _transacao("t-a", "-20.00", date(2026, 10, 5), "PIX ENVIADO FORNECEDOR A"),
        _transacao("t-b", "-10.00", date(2026, 10, 20), "PIX ENVIADO FORNECEDOR B"),
    ]

    resultado = conciliar(documentos, transacoes)

    assert [par.transacao_id for par in resultado.pares] == ["t-a", "t-b"]
