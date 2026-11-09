"""Conciliação no cenário de exemplo: o extrato gerado contra os documentos do gabarito, e as armadilhas dele."""

import json
from pathlib import Path

import pytest

from conciliador.conciliacao import candidatos, conciliar
from conciliador.extrato import ler_extrato
from conciliador.modelos import DocumentoExtraido, OrigemTexto, TransacaoBancaria

EXEMPLOS = Path(__file__).resolve().parents[1] / "dados" / "exemplos"
CENARIO = json.loads((EXEMPLOS / "cenario.json").read_text(encoding="utf-8"))
GABARITO = json.loads((EXEMPLOS / "gabarito_conciliacao.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def transacoes() -> list[TransacaoBancaria]:
    return ler_extrato((EXEMPLOS / "extrato_outubro.ofx").read_bytes(), "extrato_outubro.ofx")


@pytest.fixture
def documentos() -> dict[str, DocumentoExtraido]:
    """Os documentos como se a extração tivesse acertado tudo."""
    return {doc["id"]: DocumentoExtraido.model_validate({**doc, "origem": "xml"}) for doc in CENARIO["documentos"]}


def _fitid(memo: str, transacoes: list[TransacaoBancaria]) -> str:
    [fitid] = [t.id for t in transacoes if t.descricao == memo]
    return fitid


def _fitid_do_documento(documento_id: str) -> str:
    [fitid] = [fitid for fitid, doc in GABARITO["transacoes"].items() if doc == documento_id]
    return fitid


def _pares(resultado) -> dict[str, str]:
    return {par.transacao_id: par.documento_id for par in resultado.pares}


def test_cenario_completo_bate_100_com_o_gabarito(documentos, transacoes):
    resultado = conciliar(documentos, transacoes)

    assert _pares(resultado) == {fitid: doc for fitid, doc in GABARITO["transacoes"].items() if doc}
    assert resultado.transacoes_sem_documento == [f for f, doc in GABARITO["transacoes"].items() if doc is None]
    assert resultado.documentos_sem_transacao == GABARITO["documentos_sem_transacao"]
    assert all(par.score >= 0.55 for par in resultado.pares)


def test_armadilha_mesmo_fornecedor_e_valor_casa_pela_data(documentos, transacoes):
    """doc-01 e doc-12: Tecidos Vale Verde, R$ 3.480,00 cada, vencimentos 12/10 e 30/10."""
    esperado = {_fitid_do_documento("doc-01"): "doc-01", _fitid_do_documento("doc-12"): "doc-12"}
    so_os_dois = {doc_id: documentos[doc_id] for doc_id in ("doc-12", "doc-01")}

    for ordem in (transacoes, transacoes[::-1]):
        assert _pares(conciliar(so_os_dois, ordem)) == esperado

    [primeiro, segundo] = candidatos(documentos["doc-01"], transacoes)
    assert (primeiro.transacao.id, segundo.transacao.id) == (
        _fitid_do_documento("doc-01"),
        _fitid_do_documento("doc-12"),
    )
    assert "pago 18 dias depois do vencimento" in segundo.motivos


def test_armadilha_pix_de_mesmo_valor_para_outro_nome_nao_casa(documentos, transacoes):
    """doc-02 (Clic Contabilidade, R$ 650,00) e o PIX de R$ 650,00 para JOAO PEDRO em 21/10."""
    pix_joao = _fitid("PIX ENVIADO JOAO PEDRO S", transacoes)
    pix_clic = _fitid("PIX ENVIADO CLIC CONTABILIDADE LTDA", transacoes)

    resultado = conciliar(documentos, transacoes)
    assert _pares(resultado)[pix_clic] == "doc-02"
    assert pix_joao in resultado.transacoes_sem_documento

    sem_o_pix_da_clic = [t for t in transacoes if t.id != pix_clic]
    resultado = conciliar(documentos, sem_o_pix_da_clic)
    assert "doc-02" in resultado.documentos_sem_transacao
    assert pix_joao in resultado.transacoes_sem_documento


def test_armadilha_venda_ainda_nao_recebida_sobra(documentos, transacoes):
    resultado = conciliar(documentos, transacoes)

    assert resultado.documentos_sem_transacao == ["doc-10"]
    assert candidatos(documentos["doc-10"], transacoes) == []


@pytest.mark.parametrize("documento_id", ["doc-03", "doc-11"])
def test_armadilha_documento_sem_cnpj_casa_por_nome_e_valor(documentos, transacoes, documento_id):
    assert documentos[documento_id].contraparte_cnpj is None

    resultado = conciliar(documentos, transacoes)

    [par] = [par for par in resultado.pares if par.documento_id == documento_id]
    assert par.transacao_id == _fitid_do_documento(documento_id)
    assert any(motivo.startswith("nome parecido com o memo") for motivo in par.motivos)


def test_armadilha_documento_lido_por_ocr_casa_mesmo_com_o_nome_imperfeito(documentos, transacoes):
    """doc-05 é PDF escaneado: o OCR pode trocar letras do nome ("FlBRA") e a conciliação não pode depender disso."""
    documentos["doc-05"] = documentos["doc-05"].model_copy(
        update={"emitente_nome": "REDE SUL FlBRA LTDA", "origem": OrigemTexto.OCR, "confianca": 0.7}
    )

    resultado = conciliar(documentos, transacoes)

    assert _pares(resultado)[_fitid_do_documento("doc-05")] == "doc-05"
