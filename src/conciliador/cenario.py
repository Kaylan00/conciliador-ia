"""Leitura do cenário de exemplo (dados/exemplos/cenario.json) como objetos do contrato.

Serve para testar e avaliar cada etapa isolada da anterior: a classificação e os lançamentos são medidos
sobre o gabarito dos documentos, sem herdar erro de OCR.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path

from conciliador.modelos import (
    Direcao,
    DocumentoExtraido,
    ItemDocumento,
    OrigemTexto,
    TipoDocumento,
    TransacaoBancaria,
)

RAIZ = Path(__file__).resolve().parents[2]
ARQUIVO = RAIZ / "dados" / "exemplos" / "cenario.json"


@dataclass
class Cenario:
    empresa: dict
    documentos: dict[str, DocumentoExtraido]
    transacoes: list[TransacaoBancaria]
    conta_esperada: dict[str, str | None]  # documento_id ou transacao_id -> conta do gabarito
    par_esperado: dict[str, str | None]  # transacao_id -> documento_id (None = sem documento)


def _data(texto: str | None) -> date | None:
    return date.fromisoformat(texto) if texto else None


def carregar(caminho: Path = ARQUIVO) -> Cenario:
    bruto = json.loads(caminho.read_text(encoding="utf-8"))
    documentos: dict[str, DocumentoExtraido] = {}
    transacoes: list[TransacaoBancaria] = []
    conta_esperada: dict[str, str | None] = {}
    par_esperado: dict[str, str | None] = {}

    for d in bruto["documentos"]:
        documentos[d["id"]] = DocumentoExtraido(
            tipo=TipoDocumento(d["tipo"]),
            direcao=Direcao(d["direcao"]),
            emitente_nome=d["emitente_nome"],
            emitente_cnpj=d["emitente_cnpj"],
            destinatario_nome=d["destinatario_nome"],
            destinatario_cnpj=d["destinatario_cnpj"],
            numero=d["numero"],
            data_emissao=_data(d["data_emissao"]),
            data_vencimento=_data(d["data_vencimento"]),
            valor_total=Decimal(d["valor_total"]),
            itens=[ItemDocumento(**i) for i in d["itens"]],
            descricao=d["descricao"],
            origem=OrigemTexto.XML,
        )
        conta_esperada[d["id"]] = d["conta_esperada"]
        if d["pagamento"]:
            p = d["pagamento"]
            t_id = f"t-{d['id']}"
            transacoes.append(
                TransacaoBancaria(
                    id=t_id, data=_data(p["data"]), valor=Decimal(p["valor"]), descricao=p["memo"], tipo=p["tipo"]
                )
            )
            par_esperado[t_id] = d["id"]

    for n, t in enumerate(bruto["transacoes_sem_documento"], start=1):
        t_id = f"t-extra-{n}"
        transacoes.append(
            TransacaoBancaria(
                id=t_id, data=_data(t["data"]), valor=Decimal(t["valor"]), descricao=t["memo"], tipo=t["tipo"]
            )
        )
        conta_esperada[t_id] = t["conta_esperada"]
        par_esperado[t_id] = None

    transacoes.sort(key=lambda t: (t.data, t.id))
    return Cenario(bruto["empresa"], documentos, transacoes, conta_esperada, par_esperado)
