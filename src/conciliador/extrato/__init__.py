"""Leitura do extrato bancário: OFX 1.x (SGML), OFX 2.x (XML) e CSV de internet banking."""

from conciliador.extrato.comum import ExtratoInvalido
from conciliador.extrato.leitor import ler_extrato

__all__ = ["ExtratoInvalido", "ler_extrato"]
