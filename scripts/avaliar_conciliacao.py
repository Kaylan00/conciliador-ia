"""Avalia a conciliação sozinha, sem os erros da extração.

    python scripts/avaliar_conciliacao.py

Os documentos vêm do gabarito de cenario.json (como se a extração tivesse acertado tudo), o extrato é o OFX gerado
por gerar_extrato.py e o resultado de `conciliar` é comparado com gabarito_conciliacao.json. Imprime o relatório e
grava docs/avaliacao_conciliacao.md. O script geral de avaliação chama `avaliar()`.
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

from conciliador.conciliacao import conciliar
from conciliador.extrato import ler_extrato
from conciliador.modelos import DocumentoExtraido, OrigemTexto

RAIZ = Path(__file__).resolve().parents[1]
EXEMPLOS = RAIZ / "dados" / "exemplos"
EXTRATO = EXEMPLOS / "extrato_outubro.ofx"
RELATORIO = RAIZ / "docs" / "avaliacao_conciliacao.md"
ORIGEM_POR_FORMATO = {
    "xml_nfe": OrigemTexto.XML,
    "pdf_texto": OrigemTexto.PDF_TEXTO,
    "pdf_escaneado": OrigemTexto.OCR,
    "imagem_escaneada": OrigemTexto.OCR,
}


def documentos_do_cenario(cenario: dict) -> dict[str, DocumentoExtraido]:
    """Documentos do gabarito; o que não é do contrato (pagamento, conta_esperada...) o Pydantic ignora."""
    return {
        doc["id"]: DocumentoExtraido.model_validate({**doc, "origem": ORIGEM_POR_FORMATO[doc["formato"]]})
        for doc in cenario["documentos"]
    }


def avaliar() -> dict:
    """Números da conciliação no cenário de exemplo, prontos para virar JSON."""
    cenario = json.loads((EXEMPLOS / "cenario.json").read_text(encoding="utf-8"))
    gabarito = json.loads((EXEMPLOS / "gabarito_conciliacao.json").read_text(encoding="utf-8"))
    transacoes = ler_extrato(EXTRATO.read_bytes(), EXTRATO.name)
    resultado = conciliar(documentos_do_cenario(cenario), transacoes)

    esperado = {fitid: doc for fitid, doc in gabarito["transacoes"].items() if doc}
    sugerido = {par.transacao_id: par for par in resultado.pares}
    certos = [fitid for fitid, par in sugerido.items() if esperado.get(fitid) == par.documento_id]
    por_id = {t.id: t for t in transacoes}
    return {
        "extrato": EXTRATO.name,
        "pares_esperados": len(esperado),
        "pares_sugeridos": len(sugerido),
        "certos": len(certos),
        "errados": [fitid for fitid in sugerido if fitid not in certos],
        "faltando": [fitid for fitid in esperado if fitid not in sugerido],
        "precisao": len(certos) / len(sugerido) if sugerido else 0.0,
        "revocacao": len(certos) / len(esperado) if esperado else 1.0,
        "transacoes_sem_documento": {
            "esperadas": [fitid for fitid, doc in gabarito["transacoes"].items() if doc is None],
            "obtidas": resultado.transacoes_sem_documento,
        },
        "documentos_sem_transacao": {
            "esperados": gabarito["documentos_sem_transacao"],
            "obtidos": resultado.documentos_sem_transacao,
        },
        "pares": [
            {
                "transacao_id": par.transacao_id,
                "data": por_id[par.transacao_id].data.isoformat(),
                "valor": str(por_id[par.transacao_id].valor),
                "memo": por_id[par.transacao_id].descricao,
                "documento_id": par.documento_id,
                "esperado": esperado.get(par.transacao_id),
                "score": par.score,
                "motivos": par.motivos,
            }
            for par in resultado.pares
        ],
    }


def relatorio(numeros: dict) -> str:
    """Relatório em Markdown, sem data nem hora: rodar de novo sem mudar o código não muda o arquivo."""
    sobras_t, sobras_d = numeros["transacoes_sem_documento"], numeros["documentos_sem_transacao"]
    linhas = [
        "# Avaliação da conciliação",
        "",
        "Gerado por `python scripts/avaliar_conciliacao.py`; não edite à mão.",
        "",
        "Os documentos vêm do gabarito de `dados/exemplos/cenario.json`, como se a extração tivesse acertado tudo,",
        f"para medir só a conciliação. O extrato é `dados/exemplos/{numeros['extrato']}` e o esperado é",
        "`dados/exemplos/gabarito_conciliacao.json`.",
        "",
        "| Métrica | Resultado |",
        "| --- | --- |",
        f"| Pares esperados | {numeros['pares_esperados']} |",
        f"| Pares sugeridos | {numeros['pares_sugeridos']} |",
        f"| Certos | {numeros['certos']} |",
        f"| Errados | {_contagem(numeros['errados'])} |",
        f"| Faltando | {_contagem(numeros['faltando'])} |",
        f"| Precisão | {_porcentagem(numeros['precisao'])} |",
        f"| Revocação | {_porcentagem(numeros['revocacao'])} |",
        f"| Transações sem documento | {_sobras(sobras_t['obtidas'], sobras_t['esperadas'])} |",
        f"| Documentos sem transação | {_sobras(sobras_d['obtidos'], sobras_d['esperados'])} |",
        "",
        "## Pares sugeridos",
        "",
        "| FITID | Data | Valor | Memo | Documento | Score | Motivos |",
        "| --- | --- | ---: | --- | --- | ---: | --- |",
    ]
    for par in numeros["pares"]:
        documento = (
            par["documento_id"]
            if par["documento_id"] == par["esperado"]
            else (f"**{par['documento_id']}** (esperado: {par['esperado'] or 'nenhum'})")
        )
        linhas.append(
            f"| {par['transacao_id']} | {par['data']} | {_reais(Decimal(par['valor']))} | {par['memo']} | {documento} "
            f"| {par['score']:.3f} | {'; '.join(par['motivos'])} |"
        )
    return "\n".join(linhas) + "\n"


def _porcentagem(fracao: float) -> str:
    return f"{fracao:.1%}".replace(".", ",")


def _reais(valor: Decimal) -> str:
    return f"{valor:,.2f}".translate(str.maketrans(",.", ".,"))


def _contagem(ids: list[str]) -> str:
    return f"{len(ids)} ({', '.join(ids)})" if ids else "0"


def _sobras(obtidas: list[str], esperadas: list[str]) -> str:
    situacao = "confere" if sorted(obtidas) == sorted(esperadas) else f"esperado: {', '.join(esperadas) or 'nenhuma'}"
    return f"{', '.join(obtidas) or 'nenhuma'} ({situacao})"


def main() -> None:
    texto = relatorio(avaliar())
    RELATORIO.parent.mkdir(parents=True, exist_ok=True)
    RELATORIO.write_text(texto, encoding="utf-8")
    print(texto)
    print(f"Relatório gravado em {RELATORIO.relative_to(RAIZ)}")


if __name__ == "__main__":
    main()
