"""Extrator por modelo de linguagem, via API compatível com OpenAI chat completions (padrão: OpenRouter).

O modelo recebe o texto do documento e o JSON Schema dos campos; a resposta é validada com Pydantic. Se não
passar, o modelo recebe os erros e tem UMA nova chance. Se falhar de novo, fica o que deu para aproveitar campo a
campo, com um Problema "extracao_invalida". Direção, confiança e problemas nunca são perguntados ao modelo.

Configuração por ambiente: OPENROUTER_API_KEY (obrigatória), EXTRATOR_MODELO e EXTRATOR_URL_BASE (opcionais).
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from conciliador.extracao.cnpj import somente_digitos
from conciliador.modelos import DocumentoExtraido, ItemDocumento, OrigemTexto, Problema, TipoDocumento

logger = logging.getLogger(__name__)

URL_BASE_PADRAO = "https://openrouter.ai/api/v1"
MODELO_PADRAO = "openai/gpt-4o-mini"
PENALIDADE_EXTRACAO_INVALIDA = 0.4


class ItemExtraido(BaseModel):
    model_config = ConfigDict(extra="forbid")

    descricao: str
    quantidade: Decimal | None = Field(None, description="quantidade, com ponto decimal")
    valor_unitario: Decimal | None = Field(None, description="em reais, com ponto decimal")
    valor_total: Decimal = Field(description="em reais, com ponto decimal")


class CamposExtraidos(BaseModel):
    """Só o que dá para ler no documento. É daqui que sai o JSON Schema mandado ao modelo."""

    model_config = ConfigDict(extra="forbid")

    tipo: TipoDocumento | None = Field(None, description="nfe, nfse, fatura, recibo, boleto ou outro")
    emitente_nome: str | None = Field(
        None, description="quem emitiu: prestador do serviço, fornecedor, empresa da conta ou quem assina o recibo")
    emitente_cnpj: str | None = Field(None, description="CNPJ do emitente, 14 dígitos sem pontuação")
    destinatario_nome: str | None = Field(None, description="quem paga: tomador, cliente, quem o recibo cita")
    destinatario_cnpj: str | None = Field(None, description="CNPJ do destinatário, 14 dígitos sem pontuação")
    numero: str | None = Field(None, description="número do documento, só dígitos, sem zeros à esquerda")
    data_emissao: date | None = Field(None, description="AAAA-MM-DD")
    data_vencimento: date | None = Field(None, description="AAAA-MM-DD, só se houver vencimento escrito")
    valor_total: Decimal | None = Field(None, description="valor total a pagar, em reais, com ponto decimal")
    itens: list[ItemExtraido] = Field(default_factory=list)
    descricao: str | None = Field(None, description="resumo curto do que foi comprado ou vendido")

    @field_validator("emitente_cnpj", "destinatario_cnpj", mode="before")
    @classmethod
    def _cnpj_so_digitos(cls, valor: Any) -> Any:
        return (somente_digitos(valor) or None) if isinstance(valor, str) else valor


SCHEMA = CamposExtraidos.model_json_schema()

INSTRUCOES = f"""Você lê documentos fiscais brasileiros (NF-e, NFS-e, faturas de consumo, recibos) a partir do
texto extraído do arquivo, que pode ter erros de OCR. Responda somente com um objeto JSON que siga este JSON Schema:

{json.dumps(SCHEMA, ensure_ascii=False)}

Regras:
- Campo que não está escrito no texto fica null. Não deduza, não complete, não invente.
- CNPJ com 14 dígitos, sem pontos, barra ou traço.
- Datas no formato AAAA-MM-DD. Valores em reais com ponto decimal (1.800,00 vira 1800.00).
- Emitente é quem emitiu o documento; destinatário é quem paga."""


@dataclass(frozen=True)
class UsoLLM:
    """Custo de uma chamada, para acompanhar gasto e latência."""

    modelo: str
    latencia_s: float
    tokens_entrada: int | None = None
    tokens_saida: int | None = None


class ErroProvedorLLM(RuntimeError):
    """A API não respondeu direito (rede, autenticação, limite). Diferente de resposta inválida do modelo."""


class ExtratorLLM:
    def __init__(
        self,
        *,
        cliente: httpx.Client | None = None,
        chave_api: str | None = None,
        modelo: str | None = None,
        url_base: str | None = None,
        timeout_s: float = 60.0,
    ) -> None:
        chave = chave_api or os.environ.get("OPENROUTER_API_KEY")
        if not chave:
            raise ValueError("defina OPENROUTER_API_KEY para usar o extrator llm")
        self._chave = chave
        self.modelo = modelo or os.environ.get("EXTRATOR_MODELO") or MODELO_PADRAO
        self.url_base = (url_base or os.environ.get("EXTRATOR_URL_BASE") or URL_BASE_PADRAO).rstrip("/")
        self._cliente = cliente or httpx.Client(timeout=timeout_s)
        self.uso: list[UsoLLM] = []

    def extrair(self, texto: str, origem: OrigemTexto) -> DocumentoExtraido:
        mensagens = [
            {"role": "system", "content": INSTRUCOES},
            {"role": "user", "content": f"Origem do texto: {origem.value}\n\nTexto do documento:\n<<<\n{texto}\n>>>"},
        ]
        resposta = self._chamar(mensagens)
        campos, erro = _validar(resposta)
        if campos is not None:
            return _documento(campos, texto, origem)

        logger.info("extrator llm: resposta inválida, tentando de novo: %s", erro)
        mensagens += [
            {"role": "assistant", "content": resposta},
            {"role": "user", "content": f"A resposta não passou na validação:\n{erro}\n"
                                        "Responda de novo só com o JSON corrigido, seguindo o schema."},
        ]
        nova_resposta = self._chamar(mensagens)
        campos, novo_erro = _validar(nova_resposta)
        if campos is not None:
            return _documento(campos, texto, origem)

        parcial = _campos_aproveitaveis(nova_resposta) or _campos_aproveitaveis(resposta)
        problema = Problema(
            codigo="extracao_invalida",
            mensagem=f"O modelo não devolveu um JSON válido em duas tentativas; só os campos legíveis foram usados "
                     f"({novo_erro}).",
            penalidade=PENALIDADE_EXTRACAO_INVALIDA,
        )
        return _documento(parcial, texto, origem, [problema])

    def _chamar(self, mensagens: list[dict[str, str]]) -> str:
        corpo = {
            "model": self.modelo,
            "messages": mensagens,
            "temperature": 0,
            "response_format": {"type": "json_schema", "json_schema": {"name": "documento_fiscal", "schema": SCHEMA}},
        }
        cabecalhos = {"Authorization": f"Bearer {self._chave}", "X-Title": "conciliador-ia"}
        inicio = time.perf_counter()
        try:
            resposta = self._cliente.post(f"{self.url_base}/chat/completions", json=corpo, headers=cabecalhos)
            resposta.raise_for_status()
            dados = resposta.json()
        except (httpx.HTTPError, ValueError) as erro:
            raise ErroProvedorLLM(f"falha ao chamar {self.url_base}: {erro}") from erro
        latencia = time.perf_counter() - inicio

        uso = dados.get("usage") or {}
        registro = UsoLLM(self.modelo, round(latencia, 3), uso.get("prompt_tokens"), uso.get("completion_tokens"))
        self.uso.append(registro)
        logger.info("extrator llm: modelo=%s latencia=%.2fs tokens_entrada=%s tokens_saida=%s", registro.modelo,
                    registro.latencia_s, registro.tokens_entrada, registro.tokens_saida)
        try:
            conteudo = dados["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError):
            return ""
        return conteudo if isinstance(conteudo, str) else ""


def _json_da_resposta(resposta: str) -> Any:
    """Alguns modelos embrulham o JSON em ```json ... ``` mesmo pedindo para não embrulhar."""
    sem_cerca = re.sub(r"^\s*```(?:json)?\s*|\s*```\s*$", "", resposta)
    return json.loads(sem_cerca)


def _validar(resposta: str) -> tuple[CamposExtraidos | None, str]:
    try:
        return CamposExtraidos.model_validate(_json_da_resposta(resposta)), ""
    except json.JSONDecodeError as erro:
        return None, f"não é JSON válido: {erro.msg} (posição {erro.pos})"
    except ValidationError as erro:
        detalhes = "; ".join(f"{'.'.join(map(str, e['loc'])) or 'raiz'}: {e['msg']}" for e in erro.errors())
        return None, f"não segue o schema: {detalhes}"


def _campos_aproveitaveis(resposta: str) -> CamposExtraidos | None:
    """Valida campo a campo e fica com os que passam; o resto vira None."""
    try:
        bruto = _json_da_resposta(resposta)
    except json.JSONDecodeError:
        return None
    if not isinstance(bruto, dict):
        return None
    aproveitados: dict[str, Any] = {}
    for nome in CamposExtraidos.model_fields:
        if nome not in bruto:
            continue
        if nome == "itens":
            itens = bruto["itens"] if isinstance(bruto["itens"], list) else []
            aproveitados["itens"] = [i for i in (_item_valido(item) for item in itens) if i is not None]
            continue
        try:
            aproveitados[nome] = getattr(CamposExtraidos.model_validate({nome: bruto[nome]}), nome)
        except ValidationError:
            pass
    return CamposExtraidos.model_validate(aproveitados) if aproveitados else None


def _item_valido(item: Any) -> ItemExtraido | None:
    try:
        return ItemExtraido.model_validate(item)
    except ValidationError:
        return None


def _documento(campos: CamposExtraidos | None, texto: str, origem: OrigemTexto,
               problemas: list[Problema] | None = None) -> DocumentoExtraido:
    campos = campos or CamposExtraidos()
    itens = []
    for item in campos.itens:
        quantidade = item.quantidade or Decimal("1")
        unitario = item.valor_unitario
        if unitario is None:
            unitario = (item.valor_total / quantidade).quantize(Decimal("0.01"))
        itens.append(ItemDocumento(descricao=item.descricao, quantidade=quantidade, valor_unitario=unitario,
                                   valor_total=item.valor_total))
    return DocumentoExtraido(
        tipo=campos.tipo or TipoDocumento.OUTRO,
        emitente_nome=campos.emitente_nome,
        emitente_cnpj=campos.emitente_cnpj,
        destinatario_nome=campos.destinatario_nome,
        destinatario_cnpj=campos.destinatario_cnpj,
        numero=campos.numero,
        data_emissao=campos.data_emissao,
        data_vencimento=campos.data_vencimento,
        valor_total=campos.valor_total.quantize(Decimal("0.01")) if campos.valor_total is not None else None,
        itens=itens,
        descricao=campos.descricao,
        origem=origem,
        texto=texto,
        problemas=problemas or [],
    )
