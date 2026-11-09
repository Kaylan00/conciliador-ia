"""Classificação contábil por modelo de linguagem, para o que as regras não resolveram.

O modelo recebe só as contas folha permitidas e tem que devolver JSON {"conta": "...", "justificativa": "..."}.
Resposta fora da lista, JSON quebrado ou erro de rede viram None: quem chamou cai no "a classificar"
e uma pessoa decide. Nunca deixa um palpite inválido virar lançamento.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field

import httpx

from conciliador.plano_contas import Conta

URL_PADRAO = "https://openrouter.ai/api/v1"
MODELO_PADRAO = "meta-llama/llama-3.3-70b-instruct:free"

INSTRUCOES = (
    "Você é assistente de escrituração contábil de uma pequena empresa brasileira. "
    "Escolha UMA conta da lista para classificar o documento. Responda só com JSON no formato "
    '{"conta": "<código da lista>", "justificativa": "<uma frase>"}. '
    "Se nenhuma conta servir, responda {\"conta\": null, \"justificativa\": \"...\"}."
)


@dataclass
class ClassificadorLLM:
    cliente: httpx.Client
    modelo: str = MODELO_PADRAO
    chamadas: list[dict] = field(default_factory=list)  # tokens e latência de cada chamada, para medir custo

    @classmethod
    def do_ambiente(cls) -> ClassificadorLLM | None:
        chave = os.environ.get("OPENROUTER_API_KEY")
        if not chave:
            return None
        cliente = httpx.Client(
            base_url=os.environ.get("LLM_BASE_URL", URL_PADRAO),
            headers={"Authorization": f"Bearer {chave}"},
            timeout=60,
        )
        return cls(cliente, os.environ.get("CLASSIFICADOR_MODELO", MODELO_PADRAO))

    def __call__(self, texto: str, contas: list[Conta]) -> tuple[str, str] | None:
        lista = "\n".join(f"{c.codigo} - {c.nome}" for c in contas)
        corpo = {
            "model": self.modelo,
            "temperature": 0,
            "messages": [
                {"role": "system", "content": INSTRUCOES},
                {"role": "user", "content": f"Contas permitidas:\n{lista}\n\nDocumento:\n{texto}"},
            ],
        }
        inicio = time.perf_counter()
        try:
            resposta = self.cliente.post("/chat/completions", json=corpo)
            resposta.raise_for_status()
            dados = resposta.json()
        except (httpx.HTTPError, ValueError):
            return None
        self.chamadas.append(
            {"latencia_ms": round((time.perf_counter() - inicio) * 1000), "uso": dados.get("usage", {})}
        )
        try:
            conteudo = dados["choices"][0]["message"]["content"]
            inicio_json, fim_json = conteudo.index("{"), conteudo.rindex("}") + 1
            escolha = json.loads(conteudo[inicio_json:fim_json])
        except (KeyError, IndexError, ValueError):
            return None
        codigo = escolha.get("conta")
        if codigo not in {c.codigo for c in contas}:
            return None
        return codigo, str(escolha.get("justificativa", "")).strip()
