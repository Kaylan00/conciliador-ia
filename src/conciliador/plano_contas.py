"""Plano de contas: carrega o JSON, responde nome e natureza, e diz quais contas aceitam lançamento."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]
ARQUIVO_PADRAO = RAIZ / "dados" / "plano_de_contas.json"

# Contas de controle que os lançamentos automáticos usam
BANCO = "1.1.1"
CLIENTES = "1.1.2"
FORNECEDORES = "2.1.1"
CONTAS_A_PAGAR = "2.1.2"
A_CLASSIFICAR = "4.9.9"


@dataclass(frozen=True)
class Conta:
    codigo: str
    nome: str
    natureza: str  # "devedora" ou "credora"
    folha: bool = False
    palavras: tuple[str, ...] = field(default_factory=tuple)

    @property
    def grupo(self) -> str:
        """Primeiro nível: 1 ativo, 2 passivo, 3 receita, 4 custo/despesa."""
        return self.codigo.split(".")[0]


class PlanoDeContas:
    def __init__(self, contas: list[Conta]):
        self._contas = {c.codigo: c for c in contas}

    @classmethod
    def carregar(cls, caminho: Path = ARQUIVO_PADRAO) -> PlanoDeContas:
        dados = json.loads(caminho.read_text(encoding="utf-8"))
        return cls(
            [
                Conta(
                    codigo=c["codigo"],
                    nome=c["nome"],
                    natureza=c["natureza"],
                    folha=c.get("folha", False),
                    palavras=tuple(c.get("palavras", [])),
                )
                for c in dados["contas"]
            ]
        )

    def __getitem__(self, codigo: str) -> Conta:
        return self._contas[codigo]

    def __contains__(self, codigo: str) -> bool:
        return codigo in self._contas

    @property
    def folhas(self) -> list[Conta]:
        return [c for c in self._contas.values() if c.folha]

    def folhas_do_grupo(self, grupo: str) -> list[Conta]:
        return [c for c in self.folhas if c.grupo == grupo]

    def aceita_lancamento(self, codigo: str) -> bool:
        return codigo in self._contas and self._contas[codigo].folha


@cache
def plano_padrao() -> PlanoDeContas:
    return PlanoDeContas.carregar()
