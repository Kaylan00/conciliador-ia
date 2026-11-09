from decimal import Decimal

import pytest

from conciliador.extracao.normalizacao import (
    formatar_valor_br,
    normalizar_texto,
    valor_aparece_no_texto,
    valor_br_para_decimal,
)


@pytest.mark.parametrize(
    ("sujo", "limpo"),
    [
        ("Total R$ 1.8OO,OO", "Total R$ 1.800,00"),  # O no lugar de 0
        ("RS 95,00", "R$ 95,00"),  # $ lido como S
        ("R$1.800,00", "R$ 1.800,00"),  # R$ colado
        ("R$ 1 .800, 00", "R$ 1.800,00"),  # espaço no meio do número
        ("R$ 1. 800,00", "R$ 1.800,00"),
        ("R$ 1 800,00", "R$ 1800,00"),  # espaço no lugar do ponto de milhar
        ("R$ 1.800.00", "R$ 1.800,00"),  # ponto no lugar da vírgula decimal
        ("Vencimento: 1O/l0/2O26", "Vencimento: 10/10/2026"),  # l no lugar de 1
        ("CNPJ 52.6O1.815/OOO1-2O,", "CNPJ 52.601.815/0001-20,"),
        ("CNPJ   52.601.815/0001-20", "CNPJ 52.601.815/0001-20"),  # espaços repetidos
    ],
)
def test_corrige_numeros_sujos_de_ocr(sujo, limpo):
    assert normalizar_texto(sujo) == limpo


def test_nao_estraga_palavras_nem_listas_de_datas():
    texto = "Olá, Ilha do Boi lo O I\nLeituras: 31/08/2026, 30/09/2026"
    assert normalizar_texto(texto) == texto


@pytest.mark.parametrize(
    ("texto", "valor"),
    [("1.234,56", "1234.56"), ("R$ 95,00", "95.00"), ("1800,00", "1800.00"), ("1.800", "1800.00"), ("0,5", "0.50")],
)
def test_valor_brasileiro_para_decimal(texto, valor):
    assert valor_br_para_decimal(texto) == Decimal(valor)


def test_formatar_valor_brasileiro():
    assert formatar_valor_br(Decimal("1234.5")) == "1.234,50"
    assert formatar_valor_br(Decimal("57.8")) == "57,80"


def test_valor_aparece_no_texto_com_ou_sem_milhar_mas_nao_dentro_de_outro_numero():
    assert valor_aparece_no_texto(Decimal("1800.00"), "a importância de R$ 1.800,00")
    assert valor_aparece_no_texto(Decimal("1800.00"), "total 1800,00")
    assert valor_aparece_no_texto(Decimal("1800.00"), "R$ 1.8OO,OO")  # sujeira de OCR não esconde o valor
    assert not valor_aparece_no_texto(Decimal("1800.00"), "R$ 11.800,00")
    assert not valor_aparece_no_texto(Decimal("1800.00"), "R$ 1.800,001")
