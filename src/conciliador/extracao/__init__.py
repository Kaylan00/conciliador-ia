"""Provedores de extração de campos a partir do texto de um documento.

Todos seguem o mesmo contrato (`Extrator`) e devolvem um DocumentoExtraido sem confiança calculada: quem calcula
é documentos/confianca.py, com regras que não dependem do provedor.
"""

from __future__ import annotations

import os
from typing import Protocol

from conciliador.extracao.llm import ExtratorLLM
from conciliador.extracao.regras import ExtratorRegras
from conciliador.modelos import DocumentoExtraido, OrigemTexto


class Extrator(Protocol):
    def extrair(self, texto: str, origem: OrigemTexto) -> DocumentoExtraido: ...


def extrator_padrao() -> Extrator:
    """Escolhe pelo ambiente: EXTRATOR=regras (padrão) ou EXTRATOR=llm."""
    nome = os.environ.get("EXTRATOR", "regras").strip().lower()
    if nome == "regras":
        return ExtratorRegras()
    if nome == "llm":
        return ExtratorLLM()
    raise ValueError(f"EXTRATOR={nome!r} não existe; use 'regras' ou 'llm'")


__all__ = ["Extrator", "ExtratorLLM", "ExtratorRegras", "extrator_padrao"]
