"""Limpeza do texto antes das regras e conversão de valores no formato brasileiro.

O OCR erra de jeitos previsíveis em trechos numéricos: troca 0 por O, 1 por l ou I, lê "R$" como "RS" e enfia
espaço no meio do número ("1 .800, 00"). Corrigir isso antes das regex deixa as regras simples. A correção só
mexe em "palavras" que já são quase só número, para não estragar texto comum.
"""

from __future__ import annotations

import re
import unicodedata
from decimal import Decimal

_CONFUSOES_DIGITO = str.maketrans({"O": "0", "o": "0", "I": "1", "l": "1", "|": "1"})
# Palavra candidata a número sujo: só dígitos, letras confundíveis com dígito e pontuação de número.
_TOKEN_NUMERICO = re.compile(r"(?<!\S)(R\$)?([0-9OoIl|][0-9OoIl|.,/:\-]*)(?!\S)")
_REAL_SUJO = re.compile(r"\bR\s?[$S5§]\s*(?=[\dOo])")
_DECIMAL_COM_PONTO = re.compile(r"R\$ ?(\d{1,3}(?:\.\d{3})*|\d+)\.(\d{2})(?!\d)")
_MILHAR_COM_ESPACO = re.compile(r"R\$ ?(\d{1,3}) (\d{3},\d{2})(?!\d)")


def _corrigir_token(achado: re.Match[str]) -> str:
    prefixo, corpo = achado.group(1) or "", achado.group(2)
    nucleo = corpo.rstrip(".,:;-")
    if not any(c.isdigit() for c in nucleo):
        return achado.group(0)
    return prefixo + nucleo.translate(_CONFUSOES_DIGITO) + corpo[len(nucleo):]


def normalizar_texto(texto: str) -> str:
    """Deixa o texto pronto para as regex: Unicode composto, espaços simples e números sem sujeira de OCR."""
    texto = unicodedata.normalize("NFC", texto)
    texto = texto.replace(" ", " ").replace("–", "-").replace("—", "-")
    texto = re.sub(r"[^\S\n]+", " ", texto)
    texto = _REAL_SUJO.sub("R$ ", texto)
    texto = _TOKEN_NUMERICO.sub(_corrigir_token, texto)
    # Espaço que o OCR enfia em volta da pontuação de um número: "1 .800, 00" -> "1.800,00".
    texto = re.sub(r"(\d) ([.,])(?=\d)", r"\1\2", texto)
    texto = re.sub(r"(\d)\. (\d{3})(?=[.,]\d)", r"\1.\2", texto)
    texto = re.sub(r"(\d), (\d{2})(?![\d/.\-])", r"\1,\2", texto)  # sem engolir "31/08/2026, 30/09/2026"
    texto = _MILHAR_COM_ESPACO.sub(r"R$ \1\2", texto)
    texto = _DECIMAL_COM_PONTO.sub(r"R$ \1,\2", texto)
    return texto


def valor_br_para_decimal(texto: str) -> Decimal:
    """'R$ 1.234,56' -> Decimal('1234.56'). Ponto é milhar, vírgula é decimal."""
    limpo = re.sub(r"[^\d,]", "", texto)
    if "," not in limpo:
        return Decimal(limpo).quantize(Decimal("0.01"))
    inteiro, _, centavos = limpo.rpartition(",")
    return Decimal(f"{inteiro.replace(',', '') or 0}.{centavos}").quantize(Decimal("0.01"))


def formatar_valor_br(valor: Decimal) -> str:
    """Decimal('1234.5') -> '1.234,50'."""
    return f"{valor:,.2f}".translate(str.maketrans(",.", ".,"))


def valor_aparece_no_texto(valor: Decimal, texto: str) -> bool:
    """O valor está escrito no texto, com ou sem separador de milhar? Serve para pegar valor inventado."""
    normalizado = normalizar_texto(texto)
    com_milhar = formatar_valor_br(valor)
    sem_milhar = com_milhar.replace(".", "")
    return any(
        re.search(rf"(?<![\d.,]){re.escape(forma)}(?!\d)", normalizado) for forma in {com_milhar, sem_milhar}
    )
