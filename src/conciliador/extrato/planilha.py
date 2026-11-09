"""CSV exportado pelo internet banking: "Data;Histórico;Valor" ou com débito e crédito em colunas separadas.

Cada banco muda o delimitador, o nome das colunas, o encoding e põe linhas de saldo e de título antes da tabela.
Por isso o leitor procura a linha de cabeçalho pelos nomes das colunas em vez de supor que é a primeira.
"""

from __future__ import annotations

import csv
import io
import itertools
import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from conciliador.extrato.comum import CENTAVO, ExtratoInvalido, com_ids_unicos, id_estavel, normalizar
from conciliador.modelos import TransacaoBancaria

_DELIMITADORES = (";", ",", "\t")
_LINHAS_ATE_O_CABECALHO = 30
_DATA_BR = re.compile(r"(\d{1,2})[/.-](\d{1,2})[/.-](\d{4}|\d{2})\b")
_DATA_ISO = re.compile(r"(\d{4})-(\d{2})-(\d{2})\b")
_MILHAR_COM_PONTO = re.compile(r"\d{1,3}(\.\d{3})+")
_NUMERO = re.compile(r"\d+(\.\d+)?")


@dataclass(frozen=True)
class _Colunas:
    data: int
    descricao: int
    valor: int | None
    debito: int | None
    credito: int | None


def ler_csv(conteudo: bytes) -> list[TransacaoBancaria]:
    texto = _decodificar(conteudo)
    delimitador, linha_do_cabecalho, colunas = _localizar_cabecalho(texto)
    leitor = csv.reader(io.StringIO(texto), delimiter=delimitador)
    transacoes = []
    for celulas in leitor:
        if leitor.line_num > linha_do_cabecalho and (transacao := _transacao(celulas, colunas, leitor.line_num)):
            transacoes.append(transacao)
    return com_ids_unicos(transacoes)


def _decodificar(conteudo: bytes) -> str:
    # Banco digital exporta em UTF-8 (às vezes com BOM); planilha salva pelo Excel no Brasil sai em cp1252.
    try:
        return conteudo.decode("utf-8-sig")
    except UnicodeDecodeError:
        return conteudo.decode("cp1252", errors="replace")


def _localizar_cabecalho(texto: str) -> tuple[str, int, _Colunas]:
    for delimitador in _DELIMITADORES:
        leitor = csv.reader(io.StringIO(texto), delimiter=delimitador)
        for celulas in itertools.islice(leitor, _LINHAS_ATE_O_CABECALHO):
            if colunas := _mapear_colunas(celulas):
                return delimitador, leitor.line_num, colunas
    raise ExtratoInvalido(
        "não achei o cabeçalho do CSV: esperava colunas de data, histórico (ou descrição) e valor (ou débito e crédito)"
    )


def _mapear_colunas(celulas: list[str]) -> _Colunas | None:
    papeis: dict[str, int] = {}
    for indice, celula in enumerate(celulas):
        if (papel := _papel_da_coluna(normalizar(celula))) and papel not in papeis:
            papeis[papel] = indice
    tem_valor = "valor" in papeis or {"debito", "credito"} <= papeis.keys()
    if "data" not in papeis or "descricao" not in papeis or not tem_valor:
        return None
    return _Colunas(
        data=papeis["data"],
        descricao=papeis["descricao"],
        valor=papeis.get("valor"),
        debito=papeis.get("debito"),
        credito=papeis.get("credito"),
    )


def _papel_da_coluna(nome: str) -> str | None:
    # A ordem importa: "Data Lançamento" é data, "Valor Débito" é débito e "Saldo (R$)" não é valor.
    if nome.startswith("DATA") or nome == "DT":
        return "data"
    if "SALDO" in nome:
        return None
    if "DEBITO" in nome or nome.startswith("SAIDA"):
        return "debito"
    if "CREDITO" in nome or nome.startswith("ENTRADA"):
        return "credito"
    if nome.startswith(("VALOR", "MONTANTE", "QUANTIA")):
        return "valor"
    if nome.startswith(("HISTORICO", "DESCRICAO", "LANCAMENTO", "MEMO", "DETALHE")):
        return "descricao"
    return None


def _transacao(celulas: list[str], colunas: _Colunas, linha: int) -> TransacaoBancaria | None:
    """Transação da linha, ou None para linha de saldo, em branco ou sem valor (título, observação, rodapé)."""

    def celula(indice: int | None) -> str:
        return celulas[indice].strip() if indice is not None and indice < len(celulas) else ""

    descricao = celula(colunas.descricao)
    texto_data = celula(colunas.data)
    if _eh_saldo(descricao) or _eh_saldo(texto_data):
        return None
    colunas_de_valor = [colunas.valor] if colunas.valor is not None else [colunas.debito, colunas.credito]
    if not any(celula(indice) for indice in colunas_de_valor):
        return None

    data = _ler_data(texto_data)
    if data is None:
        raise ExtratoInvalido(f"linha {linha}: data {texto_data!r} não está no formato dd/mm/aaaa")
    if colunas.valor is not None:
        valor = _ler_numero(celula(colunas.valor), linha)
    else:  # o débito vem positivo ou negativo conforme o banco; quem dá o sinal é a coluna
        valor = abs(_ler_numero(celula(colunas.credito), linha)) - abs(_ler_numero(celula(colunas.debito), linha))
    return TransacaoBancaria(id=id_estavel(data, valor, descricao), data=data, valor=valor, descricao=descricao)


def _eh_saldo(texto: str) -> bool:
    # "SALDO ANTERIOR", "SALDO DO DIA" e o "S A L D O" espaçado de alguns bancos.
    return normalizar(texto).replace(" ", "").startswith("SALDO")


def _ler_data(texto: str) -> date | None:
    if encontrada := _DATA_BR.match(texto):
        dia, mes, ano = (int(parte) for parte in encontrada.groups())
        ano += 2000 if ano < 100 else 0
    elif encontrada := _DATA_ISO.match(texto):
        ano, mes, dia = (int(parte) for parte in encontrada.groups())
    else:
        return None
    try:
        return date(ano, mes, dia)
    except ValueError:
        return None


def _ler_numero(texto: str, linha: int) -> Decimal:
    """Número em formato brasileiro ("-3.480,00", "R$ 95,00", "1.234,56-") ou com ponto decimal ("-3480.00")."""
    if not texto:
        return Decimal("0.00")
    limpo = texto.upper().replace("R$", "").replace(" ", "")
    negativo = limpo.startswith("-") or limpo.endswith("-")
    limpo = limpo.strip("+-")
    if "," in limpo and "." in limpo:
        decimal = "," if limpo.rfind(",") > limpo.rfind(".") else "."
    elif "," in limpo or _MILHAR_COM_PONTO.fullmatch(limpo):  # "3.480" sem centavos é três mil e quatrocentos
        decimal = ","
    else:
        decimal = "."
    milhar = "." if decimal == "," else ","
    numero = limpo.replace(milhar, "").replace(decimal, ".")
    if not _NUMERO.fullmatch(numero):
        raise ExtratoInvalido(f"linha {linha}: valor {texto!r} não é um número")
    valor = Decimal(numero).quantize(CENTAVO)
    return -valor if negativo else valor
