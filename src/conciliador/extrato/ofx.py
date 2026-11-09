"""OFX 1.x (SGML) e 2.x (XML), do jeito que os bancos brasileiros exportam.

O leitor não monta a árvore do documento: no SGML as tags de campo não fecham e cada banco acrescenta as suas.
Ele recorta cada <STMTTRN> e lê só os campos que importam; o que não conhece, ignora.
"""

from __future__ import annotations

import codecs
import html
import re
from datetime import date
from decimal import Decimal, InvalidOperation

from conciliador.extrato.comum import CENTAVO, ExtratoInvalido, com_ids_unicos, id_estavel
from conciliador.modelos import TransacaoBancaria

_MARCA_OFX = re.compile(rb"OFXHEADER|<OFX>", re.IGNORECASE)
_INICIO = re.compile(r"<OFX>", re.IGNORECASE)
_INICIO_TRANSACAO = re.compile(r"<STMTTRN>", re.IGNORECASE)
_FIM_TRANSACAO = re.compile(r"</STMTTRN>|</BANKTRANLIST>", re.IGNORECASE)
_LISTA_DE_TRANSACOES = re.compile(r"<BANKTRANLIST>", re.IGNORECASE)
# Valor de um campo: o texto até a próxima tag, que no SGML é a tag seguinte e no XML é o fechamento.
_CAMPOS = {
    tag: re.compile(rf"<{tag}>([^<]*)", re.IGNORECASE)
    for tag in ("TRNTYPE", "DTPOSTED", "TRNAMT", "FITID", "MEMO", "NAME")
}
_DATA = re.compile(r"(\d{4})(\d{2})(\d{2})")
_ENCODING_XML = re.compile(r"<\?xml[^>]*encoding\s*=\s*[\"']([\w.:-]+)", re.IGNORECASE)
_CAMPO_DO_CABECALHO = re.compile(r"^\s*(\w+)\s*:(.*)$", re.MULTILINE)


def parece_ofx(conteudo: bytes) -> bool:
    return _MARCA_OFX.search(conteudo) is not None


def ler_ofx(conteudo: bytes) -> list[TransacaoBancaria]:
    texto = _decodificar(conteudo)
    inicio = _INICIO.search(texto)
    if inicio is None:
        raise ExtratoInvalido("o arquivo tem cabeçalho OFX, mas falta o bloco <OFX> com o extrato")
    corpo = texto[inicio.start() :]

    blocos = _INICIO_TRANSACAO.split(corpo)[1:]
    if not blocos and not _LISTA_DE_TRANSACOES.search(corpo):
        raise ExtratoInvalido("o OFX não tem lista de transações (BANKTRANLIST)")
    return com_ids_unicos(
        [_transacao(_FIM_TRANSACAO.split(bloco, maxsplit=1)[0], numero) for numero, bloco in enumerate(blocos, 1)]
    )


def _transacao(bloco: str, numero: int) -> TransacaoBancaria:
    campos = {tag: valor for tag, padrao in _CAMPOS.items() if (valor := _campo(padrao, bloco))}
    fitid = campos.get("FITID")
    onde = f"transação {numero}" + (f" (FITID {fitid})" if fitid else "")
    data = _data(campos.get("DTPOSTED"), onde)
    valor = _valor(campos.get("TRNAMT"), onde)
    descricao = _descricao(campos.get("MEMO"), campos.get("NAME"))
    return TransacaoBancaria(
        id=fitid or id_estavel(data, valor, descricao),
        data=data,
        valor=valor,
        descricao=descricao,
        tipo=campos.get("TRNTYPE", "").upper() or None,
    )


def _campo(padrao: re.Pattern[str], bloco: str) -> str:
    encontrado = padrao.search(bloco)
    return html.unescape(encontrado.group(1)).strip() if encontrado else ""


def _data(texto: str | None, onde: str) -> date:
    # Hora e fuso ([-3:BRT]) ficam de fora: vale o dia em que o banco lançou, que é o que o extrato mostra.
    if not texto:
        raise ExtratoInvalido(f"{onde} sem data (DTPOSTED)")
    if encontrada := _DATA.match(texto):
        try:
            return date(int(encontrada[1]), int(encontrada[2]), int(encontrada[3]))
        except ValueError:
            pass
    raise ExtratoInvalido(f"{onde}: data {texto!r} não está no formato AAAAMMDD")


def _valor(texto: str | None, onde: str) -> Decimal:
    # O OFX não tem separador de milhar. O decimal é ponto pela especificação, mas há banco que manda vírgula.
    if not texto:
        raise ExtratoInvalido(f"{onde} sem valor (TRNAMT)")
    try:
        valor = Decimal(texto.replace(",", "."))
        if valor.is_finite():
            return valor.quantize(CENTAVO)
    except InvalidOperation:
        pass
    raise ExtratoInvalido(f"{onde}: valor {texto!r} não é um número")


def _descricao(memo: str | None, nome: str | None) -> str:
    # Há banco que põe o favorecido em NAME e a operação em MEMO; junta os dois quando um não contém o outro.
    if memo and nome and nome.upper() not in memo.upper():
        return f"{memo} {nome}"
    return memo or nome or ""


def _decodificar(conteudo: bytes) -> str:
    """Decodifica pelo charset que o cabeçalho declara, com cp1252 de reserva para banco que declara errado."""
    if conteudo.startswith(codecs.BOM_UTF8):
        return conteudo[len(codecs.BOM_UTF8) :].decode("utf-8", errors="replace")
    cabecalho = conteudo[:2048].decode("latin-1")  # o cabeçalho é ASCII, e latin-1 aceita qualquer byte
    for codec in (_codec_do_cabecalho(cabecalho), "cp1252"):
        try:
            return conteudo.decode(codec)
        except UnicodeDecodeError:
            continue
    return conteudo.decode("latin-1")


def _codec_do_cabecalho(cabecalho: str) -> str:
    if "<?xml" in cabecalho.lower():  # OFX 2.x: vale a declaração XML, que por padrão é UTF-8
        declarado = _ENCODING_XML.search(cabecalho)
        return _codec_conhecido(declarado[1], padrao="utf-8") if declarado else "utf-8"

    sgml = cabecalho.split("<", 1)[0]
    campos = {chave.upper(): valor.strip().upper() for chave, valor in _CAMPO_DO_CABECALHO.findall(sgml)}
    if campos.get("ENCODING", "").replace("-", "") == "UTF8":
        return "utf-8"
    charset = campos.get("CHARSET", "")
    # "CHARSET:1252" é o cp1252; "NONE" ou nada com ENCODING:USASCII cai no cp1252, que é ASCII e ainda lê acento.
    return _codec_conhecido(f"cp{charset}" if charset.isdigit() else charset, padrao="cp1252")


def _codec_conhecido(nome: str, padrao: str) -> str:
    try:
        return codecs.lookup(nome).name
    except LookupError:
        return padrao
