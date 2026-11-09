"""CNPJ: normalização, formatação e dígito verificador (módulo 11)."""

from __future__ import annotations

import re

_PESOS_DV1 = (5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2)
_PESOS_DV2 = (6, *_PESOS_DV1)


def somente_digitos(texto: str) -> str:
    return re.sub(r"\D", "", texto)


def _digito(base: str, pesos: tuple[int, ...]) -> str:
    resto = sum(int(d) * p for d, p in zip(base, pesos, strict=True)) % 11
    return "0" if resto < 2 else str(11 - resto)


def cnpj_valido(cnpj: str) -> bool:
    """Confere os dois dígitos verificadores. Aceita com ou sem pontuação."""
    digitos = somente_digitos(cnpj)
    if len(digitos) != 14 or len(set(digitos)) == 1:  # 00.000.000/0000-00 e afins passam no módulo 11
        return False
    dv1 = _digito(digitos[:12], _PESOS_DV1)
    dv2 = _digito(digitos[:12] + dv1, _PESOS_DV2)
    return digitos[12:] == dv1 + dv2


def formatar_cnpj(cnpj: str) -> str:
    """'52601815000120' -> '52.601.815/0001-20'. Devolve como veio se não tiver 14 dígitos."""
    d = somente_digitos(cnpj)
    if len(d) != 14:
        return cnpj
    return f"{d[:2]}.{d[2:5]}.{d[5:8]}/{d[8:12]}-{d[12:]}"
