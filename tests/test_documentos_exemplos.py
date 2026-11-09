"""Os 12 arquivos de dados/exemplos/documentos/ lidos de ponta a ponta e conferidos com o gabarito do cenário."""

import functools
import importlib
import json
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from rapidfuzz import fuzz, utils

from conciliador.documentos import ler_documento
from conciliador.extracao import ExtratorRegras
from conciliador.modelos import DocumentoExtraido, OrigemTexto

RAIZ = Path(__file__).resolve().parents[1]
CENARIO = json.loads((RAIZ / "dados" / "exemplos" / "cenario.json").read_text(encoding="utf-8"))
EMPRESA = CENARIO["empresa"]["cnpj"]
GABARITO = {doc["id"]: doc for doc in CENARIO["documentos"]}
ORIGEM = {"xml_nfe": OrigemTexto.XML, "pdf_texto": OrigemTexto.PDF_TEXTO, "pdf_escaneado": OrigemTexto.OCR,
          "imagem_escaneada": OrigemTexto.OCR}


@functools.cache
def lido(doc_id: str) -> DocumentoExtraido:
    arquivo = RAIZ / "dados" / "exemplos" / "documentos" / GABARITO[doc_id]["arquivo"]
    return ler_documento(arquivo.read_bytes(), arquivo.name, cnpj_empresa=EMPRESA, extrator=ExtratorRegras())


@pytest.mark.parametrize("doc_id", GABARITO)
def test_valor_total_e_data_de_emissao_de_todos_os_documentos(doc_id):
    doc, esperado = lido(doc_id), GABARITO[doc_id]
    assert doc.valor_total == Decimal(esperado["valor_total"])
    assert doc.data_emissao == date.fromisoformat(esperado["data_emissao"])


@pytest.mark.parametrize("doc_id", GABARITO)
def test_demais_campos_do_gabarito(doc_id):
    doc, esperado = lido(doc_id), GABARITO[doc_id]
    assert doc.origem == ORIGEM[esperado["formato"]]
    assert (doc.tipo.value, doc.direcao.value) == (esperado["tipo"], esperado["direcao"])
    assert doc.emitente_cnpj == esperado["emitente_cnpj"]
    assert doc.numero == esperado["numero"]
    assert doc.data_vencimento == date.fromisoformat(esperado["data_vencimento"])
    nota = fuzz.token_sort_ratio(doc.emitente_nome or "", esperado["emitente_nome"], processor=utils.default_process)
    assert nota >= 85, doc.emitente_nome
    assert doc.confianca >= 0.8, doc.problemas


def test_armadilha_pdf_sem_camada_de_texto_passa_pelo_ocr():
    assert GABARITO["doc-05"]["formato"] == "pdf_escaneado"
    assert lido("doc-05").origem == OrigemTexto.OCR


@pytest.mark.parametrize("doc_id", ["doc-03", "doc-11"])
def test_armadilha_recibo_sem_cnpj_fica_com_nome_e_valor(doc_id):
    doc = lido(doc_id)
    assert doc.emitente_cnpj is None
    assert doc.emitente_nome == GABARITO[doc_id]["emitente_nome"]
    assert "cnpj_invalido" not in [p.codigo for p in doc.problemas]  # faltar CNPJ não é CNPJ errado


def test_armadilha_mesmo_fornecedor_e_valor_com_vencimentos_diferentes():
    primeira, reposicao = lido("doc-01"), lido("doc-12")
    assert (primeira.emitente_cnpj, primeira.valor_total) == (reposicao.emitente_cnpj, reposicao.valor_total)
    assert primeira.numero != reposicao.numero
    assert primeira.data_vencimento < reposicao.data_vencimento


def test_vendas_da_empresa_viram_receber():
    receber = {doc_id for doc_id in GABARITO if lido(doc_id).direcao.value == "receber"}
    assert receber == {"doc-09", "doc-10"}


def test_avaliar_documentos_para_o_script_geral(monkeypatch):
    monkeypatch.syspath_prepend(str(RAIZ))  # do mesmo jeito que scripts/avaliar.py importa
    resumo = importlib.import_module("scripts.avaliar_documentos").avaliar(ExtratorRegras())

    assert resumo["documentos"] == 12
    assert resumo["valor_e_data_emissao_certos"] == "12/12"
    assert set(resumo["por_formato"]) == {"xml", "pdf_texto", "pdf_escaneado", "imagem"}
    json.dumps(resumo)  # vai para o relatório geral como JSON
