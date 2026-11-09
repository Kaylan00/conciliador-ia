"""Leitura de documentos fiscais: XML de NF-e, PDF com texto, PDF escaneado e foto de recibo."""

from conciliador.documentos.leitura import (
    Formato,
    FormatoNaoSuportado,
    definir_direcao,
    detectar_formato,
    ler_documento,
)

__all__ = ["Formato", "FormatoNaoSuportado", "definir_direcao", "detectar_formato", "ler_documento"]
