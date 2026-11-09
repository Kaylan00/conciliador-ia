import json
from pathlib import Path

import pytest

from conciliador.extracao.cnpj import cnpj_valido, formatar_cnpj, somente_digitos

CENARIO = Path(__file__).resolve().parents[1] / "dados" / "exemplos" / "cenario.json"


def _cnpjs_do_cenario() -> list[str]:
    dados = json.loads(CENARIO.read_text(encoding="utf-8"))
    cnpjs = {dados["empresa"]["cnpj"]}
    for doc in dados["documentos"]:
        cnpjs |= {doc["emitente_cnpj"], doc["destinatario_cnpj"]} - {None}
    return sorted(cnpjs)


@pytest.mark.parametrize("cnpj", _cnpjs_do_cenario())
def test_cnpjs_do_cenario_tem_digito_verificador_valido(cnpj):
    assert cnpj_valido(cnpj)


@pytest.mark.parametrize("cnpj", ["11.222.333/0001-81", "11222333000181", " 52.601.815/0001-20 "])
def test_aceita_com_ou_sem_pontuacao(cnpj):
    assert cnpj_valido(cnpj)


@pytest.mark.parametrize(
    "cnpj",
    [
        "52601815000121",  # segundo dígito errado
        "52601815000110",  # primeiro dígito errado
        "52601815000102",  # dígitos trocados de lugar
        "5260181500012",  # 13 dígitos
        "526018150001200",  # 15 dígitos
        "11111111111111",  # repetido passa no módulo 11 mas não existe
        "00000000000000",
        "",
    ],
)
def test_recusa_digito_errado_tamanho_errado_e_repetidos(cnpj):
    assert not cnpj_valido(cnpj)


def test_formatacao():
    assert formatar_cnpj("52601815000120") == "52.601.815/0001-20"
    assert formatar_cnpj("123") == "123"
    assert somente_digitos("52.601.815/0001-20") == "52601815000120"
