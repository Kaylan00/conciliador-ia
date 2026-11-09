"""Provedor LLM testado só com transporte HTTP falso: nenhum teste sai para a rede."""

import json
from datetime import date
from decimal import Decimal

import httpx
import pytest

from conciliador.extracao import ExtratorLLM, ExtratorRegras, extrator_padrao
from conciliador.extracao.llm import ErroProvedorLLM
from conciliador.modelos import OrigemTexto, TipoDocumento

TEXTO = "NFS-e nº 318 ... Clic Contabilidade Ltda ... VALOR TOTAL DA NOTA = R$ 650,00"
RESPOSTA_BOA = {
    "tipo": "nfse",
    "emitente_nome": "Clic Contabilidade Ltda",
    "emitente_cnpj": "13.186.091/0001-43",
    "destinatario_nome": "Ateliê Ponto Firme Ltda",
    "destinatario_cnpj": "52601815000120",
    "numero": "318",
    "data_emissao": "2026-10-05",
    "data_vencimento": None,
    "valor_total": "650.00",
    "itens": [{"descricao": "Honorários contábeis", "quantidade": 1, "valor_total": 650}],
    "descricao": "Honorários contábeis",
}


class ProvedorFalso:
    """Responde em ordem as mensagens programadas e guarda cada pedido recebido."""

    def __init__(self, *conteudos: str, usage: bool = True) -> None:
        self.conteudos = list(conteudos)
        self.pedidos: list[httpx.Request] = []
        self.usage = usage

    def __call__(self, pedido: httpx.Request) -> httpx.Response:
        self.pedidos.append(pedido)
        corpo: dict = {"choices": [{"message": {"role": "assistant", "content": self.conteudos.pop(0)}}]}
        if self.usage:
            corpo["usage"] = {"prompt_tokens": 812, "completion_tokens": 95, "total_tokens": 907}
        return httpx.Response(200, json=corpo)

    def corpo(self, indice: int) -> dict:
        return json.loads(self.pedidos[indice].content)


def extrator_com(provedor) -> ExtratorLLM:
    cliente = httpx.Client(transport=httpx.MockTransport(provedor))
    return ExtratorLLM(cliente=cliente, chave_api="chave-de-teste", modelo="modelo/teste", url_base="https://llm.test/v1")


def test_sucesso_na_primeira_resposta():
    provedor = ProvedorFalso(json.dumps(RESPOSTA_BOA))
    extrator = extrator_com(provedor)

    doc = extrator.extrair(TEXTO, OrigemTexto.PDF_TEXTO)

    assert doc.tipo == TipoDocumento.NFSE
    assert doc.emitente_cnpj == "13186091000143"  # pontuação removida na validação
    assert doc.data_emissao == date(2026, 10, 5)
    assert doc.data_vencimento is None
    assert doc.valor_total == Decimal("650.00")
    assert doc.itens[0].valor_unitario == Decimal("650.00")  # sem unitário: total / quantidade
    assert (doc.origem, doc.texto, doc.problemas) == (OrigemTexto.PDF_TEXTO, TEXTO, [])

    pedido = provedor.pedidos[0]
    assert str(pedido.url) == "https://llm.test/v1/chat/completions"
    assert pedido.headers["Authorization"] == "Bearer chave-de-teste"
    corpo = provedor.corpo(0)
    assert corpo["model"] == "modelo/teste"
    assert corpo["temperature"] == 0
    schema = corpo["response_format"]["json_schema"]["schema"]
    assert {"emitente_cnpj", "valor_total", "data_emissao"} <= set(schema["properties"])
    assert "confianca" not in schema["properties"] and "direcao" not in schema["properties"]
    assert TEXTO in corpo["messages"][-1]["content"]
    assert "null" in corpo["messages"][0]["content"]  # o prompt manda deixar null o que não está no texto

    assert len(extrator.uso) == 1
    assert (extrator.uso[0].tokens_entrada, extrator.uso[0].tokens_saida) == (812, 95)
    assert extrator.uso[0].latencia_s >= 0


def test_aceita_json_dentro_de_bloco_de_codigo():
    provedor = ProvedorFalso(f"```json\n{json.dumps(RESPOSTA_BOA)}\n```", usage=False)
    extrator = extrator_com(provedor)
    assert extrator.extrair(TEXTO, OrigemTexto.OCR).numero == "318"
    assert extrator.uso[0].tokens_entrada is None  # sem usage na resposta, sem tokens registrados


def test_tenta_de_novo_uma_vez_quando_o_json_vem_quebrado():
    provedor = ProvedorFalso('{"tipo": "nfse", "valor_total": ', json.dumps(RESPOSTA_BOA))

    doc = extrator_com(provedor).extrair(TEXTO, OrigemTexto.PDF_TEXTO)

    assert doc.valor_total == Decimal("650.00")
    assert doc.problemas == []
    assert len(provedor.pedidos) == 2
    segunda = provedor.corpo(1)["messages"]
    assert segunda[-2] == {"role": "assistant", "content": '{"tipo": "nfse", "valor_total": '}
    assert "não é JSON válido" in segunda[-1]["content"]


def test_reenvio_leva_os_erros_de_validacao_do_schema():
    errada = {**RESPOSTA_BOA, "data_emissao": "05/10/2026"}
    provedor = ProvedorFalso(json.dumps(errada), json.dumps(RESPOSTA_BOA))

    extrator_com(provedor).extrair(TEXTO, OrigemTexto.PDF_TEXTO)

    assert "data_emissao" in provedor.corpo(1)["messages"][-1]["content"]


def test_falha_dupla_devolve_o_que_deu_e_marca_extracao_invalida():
    parcial = {"emitente_nome": "Clic Contabilidade Ltda", "data_emissao": "2026-10-05", "valor_total": "seiscentos",
               "itens": [{"descricao": "ok", "valor_total": "10.00"}, {"descricao": "sem valor"}], "campo_extra": 1}
    provedor = ProvedorFalso("não sei responder em JSON", json.dumps(parcial))

    doc = extrator_com(provedor).extrair(TEXTO, OrigemTexto.OCR)

    assert len(provedor.pedidos) == 2  # uma tentativa extra, não mais
    assert doc.emitente_nome == "Clic Contabilidade Ltda"
    assert doc.data_emissao == date(2026, 10, 5)
    assert doc.valor_total is None  # campo inválido vira None, não um chute
    assert [i.descricao for i in doc.itens] == ["ok"]
    assert doc.tipo == TipoDocumento.OUTRO
    assert [p.codigo for p in doc.problemas] == ["extracao_invalida"]
    assert doc.problemas[0].penalidade > 0


def test_falha_dupla_sem_nada_aproveitavel():
    doc = extrator_com(ProvedorFalso("[]", "null")).extrair(TEXTO, OrigemTexto.OCR)
    assert doc.valor_total is None and doc.emitente_nome is None
    assert [p.codigo for p in doc.problemas] == ["extracao_invalida"]


def test_erro_http_vira_erro_do_provedor():
    def fora_do_ar(pedido: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={"error": "indisponível"})

    with pytest.raises(ErroProvedorLLM):
        extrator_com(fora_do_ar).extrair(TEXTO, OrigemTexto.OCR)


def test_sem_chave_de_api_nao_cria_o_extrator(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    with pytest.raises(ValueError, match="OPENROUTER_API_KEY"):
        ExtratorLLM()


def test_modelo_e_url_vem_do_ambiente(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "x")
    monkeypatch.setenv("EXTRATOR_MODELO", "google/gemini-teste")
    monkeypatch.delenv("EXTRATOR_URL_BASE", raising=False)
    extrator = ExtratorLLM(cliente=httpx.Client(transport=httpx.MockTransport(ProvedorFalso())))
    assert extrator.modelo == "google/gemini-teste"
    assert extrator.url_base == "https://openrouter.ai/api/v1"


def test_extrator_padrao_pelo_ambiente(monkeypatch):
    monkeypatch.delenv("EXTRATOR", raising=False)
    assert isinstance(extrator_padrao(), ExtratorRegras)
    monkeypatch.setenv("EXTRATOR", "llm")
    monkeypatch.setenv("OPENROUTER_API_KEY", "x")
    assert isinstance(extrator_padrao(), ExtratorLLM)
    monkeypatch.setenv("EXTRATOR", "vidente")
    with pytest.raises(ValueError, match="vidente"):
        extrator_padrao()
