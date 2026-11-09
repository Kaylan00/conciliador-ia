"""Regras criadas depois de rodar o extrator por LLM (gpt-4o-mini) nos exemplos: docs/avaliacao_documentos_llm.md."""

from datetime import date
from decimal import Decimal

from conciliador.documentos.confianca import aplicar_confianca, quem_pagou_no_recibo
from conciliador.documentos.leitura import ler_documento
from conciliador.modelos import DocumentoExtraido, OrigemTexto, TipoDocumento

TEXTO = (
    "RECIBO  R$ 1.800,00\n"
    "Recebi de Ateliê Ponto Firme Ltda, CNPJ 52.601.815/0001-20, a importância de R$ 1.800,00 "
    "(mil e oitocentos reais), referente a Aluguel da sala comercial - outubro/2026.\n"
    "Cachoeiro de Itapemirim, 05/10/2026\nJosé Carlos Almeida"
)


def _recibo(emitente: str) -> DocumentoExtraido:
    return DocumentoExtraido(
        tipo=TipoDocumento.RECIBO, origem=OrigemTexto.OCR, texto=TEXTO, emitente_nome=emitente,
        valor_total=Decimal("1800.00"), data_emissao=date(2026, 10, 5),
    )


def test_acha_quem_pagou():
    assert quem_pagou_no_recibo(TEXTO) == "atelie ponto firme ltda"


def test_emitente_trocado_derruba_a_confianca():
    doc = aplicar_confianca(_recibo("Ateliê Ponto Firme Ltda"))
    assert "recibo_emitente_e_quem_pagou" in {p.codigo for p in doc.problemas}
    assert doc.confianca < 0.8


def test_emitente_certo_nao_e_penalizado():
    doc = aplicar_confianca(_recibo("José Carlos Almeida"))
    assert "recibo_emitente_e_quem_pagou" not in {p.codigo for p in doc.problemas}


class ExtratorSemVencimento:
    def extrair(self, texto, origem):
        return _recibo("José Carlos Almeida").model_copy(update={"texto": texto, "origem": origem})


def test_recibo_sem_vencimento_vence_na_data_de_emissao():
    from pathlib import Path

    imagem = Path(__file__).resolve().parents[1] / "dados/exemplos/documentos/recibo_aluguel_outubro.jpg"
    doc = ler_documento(imagem.read_bytes(), imagem.name, cnpj_empresa="52601815000120",
                        extrator=ExtratorSemVencimento())
    assert doc.data_vencimento == date(2026, 10, 5)
