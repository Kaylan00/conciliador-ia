"""Gera o extrato bancário de exemplo e o gabarito da conciliação a partir de dados/exemplos/cenario.json.

    python scripts/gerar_extrato.py

Grava em dados/exemplos/:
- extrato_outubro.ofx: OFX 1.02 em SGML, como os bancos brasileiros exportam;
- extrato_outubro.csv: a mesma movimentação como planilha de banco (Data;Histórico;Valor, UTF-8 com BOM);
- gabarito_conciliacao.json: FITID de cada transação -> id do documento (null quando não há documento).

Determinístico: rodar de novo produz exatamente os mesmos bytes.
"""

from __future__ import annotations

import csv
import html
import io
import json
from collections import Counter
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from itertools import groupby
from pathlib import Path

EXEMPLOS = Path(__file__).resolve().parents[1] / "dados" / "exemplos"
CODIGO_BANCO = "999"  # o cenário não traz o código COMPE do banco fictício
FUSO = "[-3:BRT]"
_PONTO_POR_VIRGULA = str.maketrans(",.", ".,")


@dataclass(frozen=True)
class Movimento:
    fitid: str
    data: date
    valor: Decimal
    memo: str
    tipo: str
    documento_id: str | None


def movimentos(cenario: dict) -> list[Movimento]:
    """Pagamentos dos documentos e transações sem documento em ordem de data; FITID = data + sequência no dia."""
    brutos = [(doc["pagamento"], doc["id"]) for doc in cenario["documentos"] if doc["pagamento"]]
    brutos += [(transacao, None) for transacao in cenario["transacoes_sem_documento"]]
    brutos.sort(key=lambda bruto: bruto[0]["data"])

    sequencia_no_dia: Counter[date] = Counter()
    resultado = []
    for bruto, documento_id in brutos:
        data = date.fromisoformat(bruto["data"])
        sequencia_no_dia[data] += 1
        resultado.append(
            Movimento(
                fitid=f"{data:%Y%m%d}{sequencia_no_dia[data]:03d}",
                data=data,
                valor=Decimal(bruto["valor"]),
                memo=bruto["memo"],
                tipo=bruto["tipo"],
                documento_id=documento_id,
            )
        )
    return resultado


def gerar_ofx(movimentos: list[Movimento], empresa: dict) -> bytes:
    inicio, fim = (date.fromisoformat(dia) for dia in empresa["periodo"])
    saldo_final = Decimal(empresa["saldo_inicial"]) + sum(m.valor for m in movimentos)
    linhas = [
        "OFXHEADER:100",
        "DATA:OFXSGML",
        "VERSION:102",
        "SECURITY:NONE",
        "ENCODING:USASCII",
        "CHARSET:1252",
        "COMPRESSION:NONE",
        "OLDFILEUID:NONE",
        "NEWFILEUID:NONE",
        "",
        "<OFX>",
        "<SIGNONMSGSRSV1>",
        "<SONRS>",
        "<STATUS>",
        "<CODE>0",
        "<SEVERITY>INFO",
        "</STATUS>",
        f"<DTSERVER>{_data_ofx(fim, '235959')}",
        "<LANGUAGE>POR",
        "</SONRS>",
        "</SIGNONMSGSRSV1>",
        "<BANKMSGSRSV1>",
        "<STMTTRNRS>",
        "<TRNUID>1",
        "<STATUS>",
        "<CODE>0",
        "<SEVERITY>INFO",
        "</STATUS>",
        "<STMTRS>",
        "<CURDEF>BRL",
        "<BANKACCTFROM>",
        f"<BANKID>{CODIGO_BANCO}",
        f"<BRANCHID>{empresa['agencia']}",
        f"<ACCTID>{empresa['conta']}",
        "<ACCTTYPE>CHECKING",
        "</BANKACCTFROM>",
        "<BANKTRANLIST>",
        f"<DTSTART>{_data_ofx(inicio, '000000')}",
        f"<DTEND>{_data_ofx(fim, '235959')}",
    ]
    for movimento in movimentos:
        linhas += [
            "<STMTTRN>",
            f"<TRNTYPE>{movimento.tipo}",
            f"<DTPOSTED>{_data_ofx(movimento.data)}",
            f"<TRNAMT>{movimento.valor:.2f}",
            f"<FITID>{movimento.fitid}",
            f"<MEMO>{html.escape(movimento.memo, quote=False)}",
            "</STMTTRN>",
        ]
    linhas += [
        "</BANKTRANLIST>",
        "<LEDGERBAL>",
        f"<BALAMT>{saldo_final:.2f}",
        f"<DTASOF>{_data_ofx(fim, '235959')}",
        "</LEDGERBAL>",
        "</STMTRS>",
        "</STMTTRNRS>",
        "</BANKMSGSRSV1>",
        "</OFX>",
    ]
    return ("\n".join(linhas) + "\n").encode("cp1252")


def gerar_csv(movimentos: list[Movimento], empresa: dict) -> bytes:
    """Planilha no jeito de internet banking: saldo anterior no topo e saldo do dia depois de cada dia."""
    saldo = Decimal(empresa["saldo_inicial"])
    vespera = date.fromisoformat(empresa["periodo"][0]) - timedelta(days=1)
    saida = io.StringIO()
    escritor = csv.writer(saida, delimiter=";", lineterminator="\n")
    escritor.writerow(["Data", "Histórico", "Valor"])
    escritor.writerow([f"{vespera:%d/%m/%Y}", "SALDO ANTERIOR", _reais(saldo)])
    for dia, do_dia in groupby(movimentos, key=lambda m: m.data):
        for movimento in do_dia:
            saldo += movimento.valor
            escritor.writerow([f"{dia:%d/%m/%Y}", movimento.memo, _reais(movimento.valor)])
        escritor.writerow([f"{dia:%d/%m/%Y}", "SALDO DO DIA", _reais(saldo)])
    return saida.getvalue().encode("utf-8-sig")


def gerar_gabarito(movimentos: list[Movimento], cenario: dict) -> bytes:
    pagos = {m.documento_id for m in movimentos}
    gabarito = {
        "_leia": (
            "Gerado por scripts/gerar_extrato.py a partir de cenario.json; não edite à mão. Em 'transacoes', a chave "
            "é o FITID em extrato_outubro.ofx e o valor é o documento que a transação paga ou recebe (null: nenhum)."
        ),
        "transacoes": {m.fitid: m.documento_id for m in movimentos},
        "documentos_sem_transacao": [doc["id"] for doc in cenario["documentos"] if doc["id"] not in pagos],
    }
    return (json.dumps(gabarito, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def _data_ofx(dia: date, hora: str = "120000") -> str:
    return f"{dia:%Y%m%d}{hora}{FUSO}"


def _reais(valor: Decimal) -> str:
    return f"{valor:,.2f}".translate(_PONTO_POR_VIRGULA)


def main() -> None:
    cenario = json.loads((EXEMPLOS / "cenario.json").read_text(encoding="utf-8"))
    lista = movimentos(cenario)
    arquivos = {
        "extrato_outubro.ofx": gerar_ofx(lista, cenario["empresa"]),
        "extrato_outubro.csv": gerar_csv(lista, cenario["empresa"]),
        "gabarito_conciliacao.json": gerar_gabarito(lista, cenario),
    }
    for nome, conteudo in arquivos.items():
        (EXEMPLOS / nome).write_bytes(conteudo)
        print(f"{nome}: {len(conteudo)} bytes")
    print(f"{len(lista)} transações, {sum(m.documento_id is not None for m in lista)} com documento")


if __name__ == "__main__":
    main()
