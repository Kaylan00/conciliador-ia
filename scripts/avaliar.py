"""Avaliação geral: cada etapa isolada e o fluxo inteiro, medidos contra o gabarito do cenário.

python scripts/avaliar.py            # extrator de regras (padrão, sem custo)
EXTRATOR=llm python scripts/avaliar.py

Grava docs/avaliacao.md com os números medidos nesta execução.
"""

from __future__ import annotations

import importlib
import json
import sys
from datetime import date
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ / "src"))
sys.path.insert(0, str(RAIZ))

from conciliador import cenario  # noqa: E402
from conciliador.lancamentos import classificar_documento, classificar_transacao  # noqa: E402


def _talvez(modulo: str):
    try:
        return importlib.import_module(modulo)
    except ImportError:
        return None


def avaliar_classificacao(cen: cenario.Cenario) -> dict:
    """Classificação sobre o gabarito dos documentos (isola o erro de leitura)."""
    certos, total, erros = 0, 0, []
    for doc_id, doc in cen.documentos.items():
        total += 1
        conta = classificar_documento(doc).conta
        if conta == cen.conta_esperada[doc_id]:
            certos += 1
        else:
            erros.append(f"{doc_id}: {conta} (esperado {cen.conta_esperada[doc_id]})")
    for t in cen.transacoes:
        if cen.par_esperado.get(t.id) is None and cen.conta_esperada.get(t.id):
            total += 1
            conta = classificar_transacao(t).conta
            certos += conta == cen.conta_esperada[t.id]
    return {"certos": certos, "total": total, "erros": erros}


def avaliar_fluxo_completo(cen: cenario.Cenario) -> dict | None:
    """Do arquivo ao lançamento: OCR/leitura -> extrato -> conciliação -> classificação."""
    documentos_mod, extrato_mod, conciliacao_mod = (
        _talvez("conciliador.documentos"),
        _talvez("conciliador.extrato"),
        _talvez("conciliador.conciliacao"),
    )
    pasta = RAIZ / "dados" / "exemplos"
    gabarito_arquivo = pasta / "gabarito_conciliacao.json"
    if not (documentos_mod and extrato_mod and conciliacao_mod and gabarito_arquivo.exists()):
        return None

    bruto = json.loads((pasta / "cenario.json").read_text(encoding="utf-8"))
    por_arquivo = {d["arquivo"]: d["id"] for d in bruto["documentos"]}
    docs = {}
    for arquivo, doc_id in por_arquivo.items():
        caminho = pasta / "documentos" / arquivo
        docs[doc_id] = documentos_mod.ler_documento(
            caminho.read_bytes(), arquivo, cnpj_empresa=bruto["empresa"]["cnpj"]
        )
    transacoes = extrato_mod.ler_extrato((pasta / "extrato_outubro.ofx").read_bytes(), "extrato_outubro.ofx")
    resultado = conciliacao_mod.conciliar(docs, transacoes)

    esperado = {k: v for k, v in json.loads(gabarito_arquivo.read_text(encoding="utf-8")).items() if v}
    obtido = {p.transacao_id: p.documento_id for p in resultado.pares}
    pares_certos = sum(1 for t, d in obtido.items() if esperado.get(t) == d)

    contas_certas = sum(
        1 for doc_id, doc in docs.items() if classificar_documento(doc).conta == cen.conta_esperada[doc_id]
    )
    return {
        "pares_certos": pares_certos,
        "pares_obtidos": len(obtido),
        "pares_esperados": len(esperado),
        "contas_certas": contas_certas,
        "documentos": len(docs),
        "confianca_media": round(sum(d.confianca for d in docs.values()) / len(docs), 3),
        "abaixo_de_80": sorted(i for i, d in docs.items() if d.confianca < 0.8),
    }


def main() -> None:
    cen = cenario.carregar()
    linhas = [f"# Avaliação ({date.today():%d/%m/%Y})", "", "Gerado por `python scripts/avaliar.py`.", ""]

    cls = avaliar_classificacao(cen)
    linhas += [
        "## Classificação no plano de contas (sobre o gabarito)",
        f"- Acertos: **{cls['certos']} de {cls['total']}** (12 documentos + tarifa e rendimento)",
        *(f"- Erro: {e}" for e in cls["erros"]),
        "",
    ]

    for nome in ("scripts.avaliar_documentos", "scripts.avaliar_conciliacao"):
        mod = _talvez(nome)
        if mod and hasattr(mod, "avaliar"):
            resultado = mod.avaliar()
            linhas += [f"## {nome.split('.')[-1]}", "```", json.dumps(resultado, ensure_ascii=False, indent=2,
                                                                       default=str), "```", ""]

    fluxo = avaliar_fluxo_completo(cen)
    if fluxo:
        linhas += [
            "## Fluxo completo: arquivos de exemplo até o lançamento",
            f"- Conciliação: **{fluxo['pares_certos']} de {fluxo['pares_esperados']}** pares certos"
            f" ({fluxo['pares_obtidos']} propostos)",
            f"- Classificação dos documentos lidos: **{fluxo['contas_certas']} de {fluxo['documentos']}**",
            f"- Confiança média da leitura: {fluxo['confianca_media']}",
            f"- Documentos que foram para revisão (confiança < 0,8): {', '.join(fluxo['abaixo_de_80']) or 'nenhum'}",
            "",
        ]

    texto = "\n".join(linhas)
    print(texto)
    (RAIZ / "docs").mkdir(exist_ok=True)
    (RAIZ / "docs" / "avaliacao.md").write_text(texto, encoding="utf-8")


if __name__ == "__main__":
    main()
