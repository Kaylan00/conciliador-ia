from decimal import Decimal

import pytest

from conciliador.extracao.extenso import ler_extenso, valor_por_extenso_no_texto


@pytest.mark.parametrize(
    ("extenso", "valor"),
    [
        ("mil e oitocentos reais", "1800.00"),
        ("noventa e cinco reais", "95.00"),
        ("cinquenta e sete reais e oitenta centavos", "57.80"),
        ("dois mil duzentos e oitenta reais", "2280.00"),
        ("mil cento e cinquenta reais", "1150.00"),
        ("cem reais", "100.00"),
        ("um real e um centavo", "1.01"),
        ("oitenta centavos", "0.80"),
        ("um milhão de reais", "1000000.00"),
        ("Quatrocentos e Doze Reais e Trinta e Sete Centavos", "412.37"),
    ],
)
def test_le_valor_por_extenso(extenso, valor):
    assert ler_extenso(extenso) == Decimal(valor)


@pytest.mark.parametrize("lixo", ["mil e ====srass 255", "raves € CINCO Tosis", "reais", "noventa e cinco", ""])
def test_nao_confere_o_que_nao_da_para_ler(lixo):
    assert ler_extenso(lixo) is None


def test_acha_o_extenso_entre_parenteses_no_texto():
    texto = "Valor (R$)\nimportância de R$ 57,80 (cinquenta e sete reais e oitenta\n\ncentavos), referente a"
    assert valor_por_extenso_no_texto(texto) == Decimal("57.80")
    assert valor_por_extenso_no_texto("Total a pagar: R$ 412,37") is None
