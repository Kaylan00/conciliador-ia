"""PDF: texto da camada de texto (pypdf) e renderização de páginas para OCR (pypdfium2)."""

from __future__ import annotations

import io
from collections.abc import Iterator

import pypdfium2 as pdfium
from PIL import Image
from pypdf import PdfReader
from pypdf.errors import PyPdfError

DPI_OCR = 300  # resolução em que o Tesseract rende melhor com texto de corpo 8-12 pt


class PdfInvalido(ValueError):
    """O conteúdo não é um PDF legível."""


def textos_das_paginas(conteudo: bytes) -> list[str]:
    """Texto de cada página. Página escaneada devolve texto vazio (ou quase)."""
    try:
        leitor = PdfReader(io.BytesIO(conteudo))
        # Muito PDF vem "protegido" só com senha de dono e senha de usuário vazia: abre sem pedir nada.
        if leitor.is_encrypted and not leitor.decrypt(""):
            raise PdfInvalido("PDF protegido por senha")
        return [pagina.extract_text() or "" for pagina in leitor.pages]
    except PyPdfError as erro:
        raise PdfInvalido(f"PDF ilegível: {erro}") from erro


def renderizar_paginas(conteudo: bytes, indices: list[int], dpi: int = DPI_OCR) -> Iterator[tuple[int, Image.Image]]:
    """Renderiza só as páginas pedidas, uma por vez, para não segurar todas na memória."""
    try:
        documento = pdfium.PdfDocument(conteudo)
    except pdfium.PdfiumError as erro:
        raise PdfInvalido(f"PDF ilegível: {erro}") from erro
    try:
        for indice in indices:
            pagina = documento[indice]
            try:
                yield indice, pagina.render(scale=dpi / 72, grayscale=True).to_pil()
            finally:
                pagina.close()
    finally:
        documento.close()
