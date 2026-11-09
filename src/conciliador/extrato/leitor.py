"""Porta de entrada: descobre o formato pelo conteúdo e chama o leitor certo."""

from __future__ import annotations

from conciliador.extrato.comum import ExtratoInvalido
from conciliador.extrato.ofx import ler_ofx, parece_ofx
from conciliador.extrato.planilha import ler_csv
from conciliador.modelos import TransacaoBancaria

_BINARIOS = (
    (b"%PDF", "é um PDF"),
    (b"PK\x03\x04", "é uma planilha do Excel (.xlsx) ou um arquivo compactado"),
    (b"\xd0\xcf\x11\xe0", "é uma planilha do Excel antiga (.xls)"),
    (b"\x89PNG", "é uma imagem PNG"),
    (b"\xff\xd8\xff", "é uma imagem JPEG"),
)
_EXTENSOES_OFX = (".ofx", ".qfx")


def ler_extrato(conteudo: bytes, nome_arquivo: str) -> list[TransacaoBancaria]:
    """Lê um extrato em OFX 1.x (SGML), OFX 2.x (XML) ou CSV, na ordem em que as transações aparecem.

    O formato vem do conteúdo; a extensão só deixa o erro mais claro quando o arquivo não é o que diz ser.
    Levanta ExtratoInvalido, com o nome do arquivo e o motivo em português, quando não dá para ler.
    """
    try:
        return _ler(conteudo, nome_arquivo)
    except ExtratoInvalido as erro:
        raise ExtratoInvalido(f"{nome_arquivo}: {erro}") from None


def _ler(conteudo: bytes, nome_arquivo: str) -> list[TransacaoBancaria]:
    if not conteudo.strip():
        raise ExtratoInvalido("o arquivo está vazio")
    for assinatura, o_que_e in _BINARIOS:
        if conteudo.startswith(assinatura):
            raise ExtratoInvalido(f"não é um extrato, {o_que_e}. Exporte o extrato do banco em OFX ou CSV")
    if b"\x00" in conteudo[:4096]:
        raise ExtratoInvalido("não é um extrato, é um arquivo binário. Exporte o extrato do banco em OFX ou CSV")

    if parece_ofx(conteudo):
        return ler_ofx(conteudo)
    if nome_arquivo.lower().endswith(_EXTENSOES_OFX):
        raise ExtratoInvalido("a extensão é OFX, mas o conteúdo não tem o cabeçalho OFX nem a tag <OFX>")
    return ler_csv(conteudo)
