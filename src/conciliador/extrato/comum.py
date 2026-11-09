"""Peças comuns aos leitores de extrato."""

from __future__ import annotations

import hashlib
import unicodedata
from collections import Counter
from datetime import date
from decimal import Decimal

from conciliador.modelos import TransacaoBancaria

CENTAVO = Decimal("0.01")


class ExtratoInvalido(ValueError):
    """O arquivo não é um extrato que o sistema saiba ler. A mensagem diz o porquê, para a pessoa que enviou."""


def normalizar(texto: str) -> str:
    """Maiúsculas, sem acento e com espaços simples: ' Histórico  ' vira 'HISTORICO'."""
    sem_acento = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode("ascii")
    return " ".join(sem_acento.upper().split())


def id_estavel(data: date, valor: Decimal, descricao: str) -> str:
    """Hash curto da linha normalizada: a mesma linha dá o mesmo id em importações diferentes."""
    chave = f"{data.isoformat()}|{valor:.2f}|{normalizar(descricao)}"
    return hashlib.sha256(chave.encode()).hexdigest()[:12]


def com_ids_unicos(transacoes: list[TransacaoBancaria]) -> list[TransacaoBancaria]:
    """Id repetido no mesmo arquivo ganha sufixo (-2, -3...).

    Acontece com banco que repete FITID e com dois lançamentos idênticos no mesmo dia (dois PIX de R$ 10,00 para a
    mesma pessoa). A ordem do arquivo decide quem leva o sufixo, então reimportar o mesmo arquivo dá os mesmos ids.
    """
    vistos: Counter[str] = Counter()
    unicas = []
    for transacao in transacoes:
        vistos[transacao.id] += 1
        if vistos[transacao.id] > 1:
            transacao = transacao.model_copy(update={"id": f"{transacao.id}-{vistos[transacao.id]}"})
        unicas.append(transacao)
    return unicas
