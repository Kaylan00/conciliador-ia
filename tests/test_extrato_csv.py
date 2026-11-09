from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from conciliador.extrato import ExtratoInvalido, ler_extrato

EXEMPLOS = Path(__file__).resolve().parents[1] / "dados" / "exemplos"


def _ler(texto: str, encoding: str = "utf-8"):
    return ler_extrato(texto.encode(encoding), "extrato.csv")


def test_csv_de_exemplo_tem_a_mesma_movimentacao_do_ofx_sem_as_linhas_de_saldo():
    ofx = ler_extrato((EXEMPLOS / "extrato_outubro.ofx").read_bytes(), "extrato_outubro.ofx")
    csv = ler_extrato((EXEMPLOS / "extrato_outubro.csv").read_bytes(), "extrato_outubro.csv")

    assert [(t.data, t.valor, t.descricao) for t in csv] == [(t.data, t.valor, t.descricao) for t in ofx]
    assert len({t.id for t in csv}) == len(csv)


def test_debito_e_credito_em_colunas_separadas_com_titulo_antes_da_tabela():
    texto = (
        "Extrato de conta corrente\n"
        "Agência: 0001;Conta: 12345-6\n"
        "\n"
        "Data Lançamento;Lançamento;Documento;Débito (R$);Crédito (R$);Saldo (R$)\n"
        "30/09/2026;SALDO ANTERIOR;;;;18.450,00\n"
        "12/10/2026;PAG BOLETO TECIDOS VALE VERDE LTDA;4521;3.480,00;;14.970,00\n"
        "12/10/2026;SALDO DO DIA;;;;14.970,00\n"
        "20/10/2026;PIX RECEBIDO LOJA BEM VESTIR LTDA;;;5.200,00;20.170,00\n"
        "21/10/2026;PIX ENVIADO JOAO PEDRO S;;-650,00;;19.520,00\n"
        "31/10/2026;S A L D O;;;;19.520,00\n"
    )

    transacoes = _ler(texto)

    assert [(t.data, t.valor, t.descricao) for t in transacoes] == [
        (date(2026, 10, 12), Decimal("-3480.00"), "PAG BOLETO TECIDOS VALE VERDE LTDA"),
        (date(2026, 10, 20), Decimal("5200.00"), "PIX RECEBIDO LOJA BEM VESTIR LTDA"),
        (date(2026, 10, 21), Decimal("-650.00"), "PIX ENVIADO JOAO PEDRO S"),
    ]


def test_delimitador_virgula_com_valor_entre_aspas():
    texto = (
        'Data,Descrição,Valor\n07/10/2026,"PIX ENVIADO CLIC, CONTABILIDADE","-650,00"\n31/10/2026,RENDIMENTO,"3,12"\n'
    )

    transacoes = _ler(texto)

    assert [(t.descricao, t.valor) for t in transacoes] == [
        ("PIX ENVIADO CLIC, CONTABILIDADE", Decimal("-650.00")),
        ("RENDIMENTO", Decimal("3.12")),
    ]


def test_planilha_salva_em_cp1252_com_acento():
    texto = "Data;Histórico;Valor\n05/10/2026;TED ENVIADA JOSÉ CONCEIÇÃO;-1.800,00\n"

    [transacao] = _ler(texto, encoding="cp1252")

    assert transacao.descricao == "TED ENVIADA JOSÉ CONCEIÇÃO"


@pytest.mark.parametrize(
    ("texto", "esperado"),
    [
        ("-3.480,00", "-3480.00"),
        ("3480,00", "3480.00"),
        ("-3480.00", "-3480.00"),
        ("1.234.567,89", "1234567.89"),
        ("R$ -95,00", "-95.00"),
        ("95,00-", "-95.00"),
        ("3.480", "3480.00"),
        ("3.12", "3.12"),
    ],
)
def test_numeros_em_formato_brasileiro(texto, esperado):
    [transacao] = _ler(f'Data;Histórico;Valor\n12/10/2026;X;"{texto}"\n')

    assert transacao.valor == Decimal(esperado)


@pytest.mark.parametrize("texto", ["12/10/2026", "12/10/26", "12.10.2026", "2026-10-12", "12/10/2026 14:31"])
def test_formatos_de_data(texto):
    [transacao] = _ler(f"Data;Histórico;Valor\n{texto};X;-1,00\n")

    assert transacao.data == date(2026, 10, 12)


def test_mesma_linha_da_o_mesmo_id_em_importacoes_diferentes():
    linha = "12/10/2026;PAG BOLETO TECIDOS VALE VERDE LTDA;-3.480,00\n"
    setembro_e_outubro = _ler("Data;Histórico;Valor\n30/09/2026;TARIFA;-24,90\n" + linha)
    so_outubro = _ler("Data;Descrição;Valor (R$)\n" + linha.replace("-3.480,00", "-3480.00"))

    assert setembro_e_outubro[1].id == so_outubro[0].id


def test_duas_linhas_identicas_no_mesmo_arquivo_tem_ids_diferentes():
    linha = "14/10/2026;PIX ENVIADO LEVA E TRAZ ENTR;-10,00\n"

    primeira, segunda = _ler("Data;Histórico;Valor\n" + linha + linha)

    assert segunda.id == f"{primeira.id}-2"


def test_linha_com_valor_e_data_invalida_aponta_a_linha():
    texto = "Data;Histórico;Valor\n12/10/2026;X;-1,00\n31/02/2026;Y;-2,00\n"

    with pytest.raises(ExtratoInvalido, match=r"extrato\.csv: linha 3: data '31/02/2026'"):
        _ler(texto)


def test_valor_que_nao_e_numero_aponta_a_linha():
    with pytest.raises(ExtratoInvalido, match=r"linha 2: valor 'abc' não é um número"):
        _ler("Data;Histórico;Valor\n12/10/2026;X;abc\n")


def test_csv_sem_cabecalho_de_extrato_da_erro_claro():
    with pytest.raises(ExtratoInvalido, match="não achei o cabeçalho do CSV"):
        _ler("nome;email\nAna;ana@example.com\n")
