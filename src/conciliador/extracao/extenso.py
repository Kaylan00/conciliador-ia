"""Valor por extenso ("mil e oitocentos reais") para Decimal.

Recibo e cheque trazem o valor duas vezes, em algarismos e por extenso. O OCR troca 5 por 8 num número sem
ninguém perceber, mas dificilmente transforma "noventa e cinco" em outra palavra válida: comparar os dois pega o
erro silencioso.
"""

from __future__ import annotations

import re
from decimal import Decimal

_VALORES = {
    "zero": 0, "um": 1, "uma": 1, "dois": 2, "duas": 2, "três": 3, "tres": 3, "quatro": 4, "cinco": 5, "seis": 6,
    "sete": 7, "oito": 8, "nove": 9, "dez": 10, "onze": 11, "doze": 12, "treze": 13, "catorze": 14, "quatorze": 14,
    "quinze": 15, "dezesseis": 16, "dezessete": 17, "dezoito": 18, "dezenove": 19, "vinte": 20, "trinta": 30,
    "quarenta": 40, "cinquenta": 50, "sessenta": 60, "setenta": 70, "oitenta": 80, "noventa": 90, "cem": 100,
    "cento": 100, "duzentos": 200, "duzentas": 200, "trezentos": 300, "trezentas": 300, "quatrocentos": 400,
    "quatrocentas": 400, "quinhentos": 500, "quinhentas": 500, "seiscentos": 600, "seiscentas": 600,
    "setecentos": 700, "setecentas": 700, "oitocentos": 800, "oitocentas": 800, "novecentos": 900,
    "novecentas": 900,
}
_MULTIPLICADORES = {"mil": 1_000, "milhão": 1_000_000, "milhao": 1_000_000, "milhões": 1_000_000,
                    "milhoes": 1_000_000}
_LIGACOES = {"e", "de"}
# No recibo o extenso vem entre parênteses logo depois do valor: "R$ 95,00 (noventa e cinco reais)".
_ENTRE_PARENTESES = re.compile(r"\(([^()]*?\b(?:reais|real|centavos?)\b[^()]*?)\)", re.IGNORECASE)


def _inteiro(palavras: list[str]) -> int | None:
    total = parcial = 0
    for palavra in palavras:
        if palavra in _LIGACOES:
            continue
        if palavra in _MULTIPLICADORES:
            total += (parcial or 1) * _MULTIPLICADORES[palavra]
            parcial = 0
        elif palavra in _VALORES:
            parcial += _VALORES[palavra]
        else:
            return None  # palavra desconhecida (ou lixo de OCR): melhor não conferir do que conferir errado
    return total + parcial


def ler_extenso(trecho: str) -> Decimal | None:
    """'cinquenta e sete reais e oitenta centavos' -> Decimal('57.80'); None se não der para ler com certeza."""
    palavras = re.findall(r"[a-zà-ú]+", trecho.lower())
    reais_palavras, centavos_palavras = palavras, []
    for moeda in ("reais", "real"):
        if moeda in palavras:
            corte = palavras.index(moeda)
            reais_palavras, centavos_palavras = palavras[:corte], palavras[corte + 1:]
            break
    else:
        reais_palavras, centavos_palavras = [], palavras

    centavos: int | None = 0
    if centavos_palavras:
        unidade = next((p for p in ("centavos", "centavo") if p in centavos_palavras), None)
        if unidade is None or centavos_palavras[-1] != unidade:
            return None
        centavos = _inteiro(centavos_palavras[:-1])
    reais = _inteiro(reais_palavras)
    if reais is None or centavos is None or centavos >= 100 or (reais == 0 and centavos == 0):
        return None
    return (Decimal(reais) + Decimal(centavos) / 100).quantize(Decimal("0.01"))


def valor_por_extenso_no_texto(texto: str) -> Decimal | None:
    achado = _ENTRE_PARENTESES.search(texto)
    return ler_extenso(achado.group(1)) if achado else None
