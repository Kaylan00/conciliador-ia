"""Avalia a leitura de documentos contra o gabarito do cenário.

    python scripts/avaliar_documentos.py                  # extrator de regras (padrão, sem custo)
    python scripts/avaliar_documentos.py --extrator llm   # precisa de OPENROUTER_API_KEY

Lê cada arquivo de dados/exemplos/documentos/ com `ler_documento`, do mesmo jeito que o sistema lê, e compara
campo a campo com cenario.json. Depois piora de propósito as imagens que passam por OCR (teste de estresse) para
mostrar onde a leitura quebra. Imprime as tabelas e grava docs/avaliacao_documentos.md com os números desta
execução. O script geral (scripts/avaliar.py) chama `avaliar()`, que é só a primeira parte.
"""

from __future__ import annotations

import argparse
import io
import json
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from pathlib import Path

import numpy as np
import pytesseract
from PIL import Image, ImageFilter
from rapidfuzz import fuzz, utils

from conciliador.documentos import ler_documento
from conciliador.documentos.ocr import abrir_imagem
from conciliador.documentos.pdf import renderizar_paginas
from conciliador.extracao import Extrator, ExtratorLLM, ExtratorRegras, extrator_padrao
from conciliador.modelos import DocumentoExtraido

RAIZ = Path(__file__).resolve().parents[1]
EXEMPLOS = RAIZ / "dados" / "exemplos"
DOCUMENTOS = EXEMPLOS / "documentos"
RELATORIO = RAIZ / "docs" / "avaliacao_documentos.md"

CAMPOS = ("tipo", "direcao", "emitente_cnpj", "emitente_nome", "numero", "data_emissao", "data_vencimento",
          "valor_total")
GRUPOS = {"xml_nfe": "xml", "pdf_texto": "pdf_texto", "pdf_escaneado": "pdf_escaneado",
          "imagem_escaneada": "imagem"}
SIMILARIDADE_MINIMA_NOME = 85  # rapidfuzz token_sort_ratio, sem diferenciar maiúscula e pontuação
SEMENTE_ESTRESSE = 2026
CONFIANCA_REVISAO = 0.8  # abaixo disso o documento vai para revisão (mesmo corte do scripts/avaliar.py)


@dataclass
class Comparacao:
    esperado: object
    obtido: object
    certo: bool


@dataclass
class ResultadoDocumento:
    id: str
    arquivo: str
    grupo: str
    confianca: float
    problemas: list[str]
    segundos: float
    campos: dict[str, Comparacao] = field(default_factory=dict)

    @property
    def acertos(self) -> int:
        return sum(c.certo for c in self.campos.values())

    @property
    def valor_e_data_certos(self) -> bool:
        return self.campos["valor_total"].certo and self.campos["data_emissao"].certo


def carregar_cenario() -> dict:
    return json.loads((EXEMPLOS / "cenario.json").read_text(encoding="utf-8"))


def _converter(campo: str, bruto: object) -> object:
    if bruto is None:
        return None
    if campo in ("data_emissao", "data_vencimento"):
        return date.fromisoformat(str(bruto))
    if campo == "valor_total":
        return Decimal(str(bruto))
    return str(bruto)


def comparar(campo: str, esperado_bruto: object, documento: DocumentoExtraido) -> Comparacao:
    esperado = _converter(campo, esperado_bruto)
    obtido = getattr(documento, campo)
    if campo in ("tipo", "direcao"):
        obtido = obtido.value
    if campo == "emitente_nome" and esperado is not None and obtido is not None:
        nota = fuzz.token_sort_ratio(esperado, obtido, processor=utils.default_process)
        return Comparacao(esperado, obtido, nota >= SIMILARIDADE_MINIMA_NOME)
    return Comparacao(esperado, obtido, esperado == obtido)


def avaliar_arquivo(gabarito: dict, conteudo: bytes, nome: str, cnpj_empresa: str,
                    extrator: Extrator) -> ResultadoDocumento:
    inicio = time.perf_counter()
    documento = ler_documento(conteudo, nome, cnpj_empresa=cnpj_empresa, extrator=extrator)
    return ResultadoDocumento(
        id=gabarito["id"], arquivo=gabarito["arquivo"], grupo=GRUPOS[gabarito["formato"]],
        confianca=documento.confianca, problemas=[p.codigo for p in documento.problemas],
        segundos=round(time.perf_counter() - inicio, 2),
        campos={campo: comparar(campo, gabarito[campo], documento) for campo in CAMPOS},
    )


def avaliar_detalhado(extrator: Extrator | None = None) -> list[ResultadoDocumento]:
    cenario = carregar_cenario()
    extrator = extrator or extrator_padrao()
    return [
        avaliar_arquivo(gabarito, (DOCUMENTOS / gabarito["arquivo"]).read_bytes(), gabarito["arquivo"],
                        cenario["empresa"]["cnpj"], extrator)
        for gabarito in cenario["documentos"]
    ]


def resumir(resultados: list[ResultadoDocumento]) -> dict:
    grupos = list(dict.fromkeys(GRUPOS.values()))
    matriz = {campo: {grupo: [0, 0] for grupo in grupos} for campo in CAMPOS}
    for resultado in resultados:
        for campo, comparacao in resultado.campos.items():
            matriz[campo][resultado.grupo][0] += comparacao.certo
            matriz[campo][resultado.grupo][1] += 1
    por_campo = {campo: _soma(matriz[campo].values()) for campo in CAMPOS}
    por_formato = {grupo: _soma(matriz[campo][grupo] for campo in CAMPOS) for grupo in grupos}
    return {
        "documentos": len(resultados),
        "acertos": _soma(por_campo.values()),
        "por_campo": por_campo,
        "por_formato": por_formato,
        "matriz": matriz,
        "valor_e_data_emissao_certos": f"{sum(r.valor_e_data_certos for r in resultados)}/{len(resultados)}",
        "erros": [
            {"documento": r.id, "campo": campo, "esperado": str(c.esperado), "obtido": str(c.obtido)}
            for r in resultados for campo, c in r.campos.items() if not c.certo
        ],
        "confianca": {r.id: r.confianca for r in resultados},
    }


def _soma(pares) -> list[int]:
    pares = list(pares)
    return [sum(p[0] for p in pares), sum(p[1] for p in pares)]


def avaliar(extrator: Extrator | None = None) -> dict:
    """Resumo pronto para virar JSON (é o que o scripts/avaliar.py inclui no relatório geral)."""
    return resumir(avaliar_detalhado(extrator))


# --------------------------------------------------------------------------- teste de estresse do OCR


def _ruido(sigma: float) -> Callable[[Image.Image, np.random.Generator], Image.Image]:
    def aplicar(imagem: Image.Image, rng: np.random.Generator) -> Image.Image:
        pixels = np.asarray(imagem, dtype=np.float32) + rng.normal(0, sigma, (imagem.height, imagem.width))
        return Image.fromarray(np.clip(pixels, 0, 255).astype(np.uint8))
    return aplicar


def _jpeg(qualidade: int) -> Callable[[Image.Image, np.random.Generator], Image.Image]:
    def aplicar(imagem: Image.Image, rng: np.random.Generator) -> Image.Image:
        saida = io.BytesIO()
        imagem.save(saida, "JPEG", quality=qualidade)
        return Image.open(saida)
    return aplicar


# Cada degradação vem sozinha, por cima do arquivo versionado (que já é torto, ruidoso e borrado).
DEGRADACOES: dict[str, Callable[[Image.Image, np.random.Generator], Image.Image]] = {
    "nenhuma (arquivo versionado)": lambda imagem, rng: imagem,
    "mais 3° de rotação": lambda imagem, rng: imagem.rotate(3, resample=Image.Resampling.BICUBIC, expand=True,
                                                            fillcolor=255),
    "mais 6° de rotação": lambda imagem, rng: imagem.rotate(6, resample=Image.Resampling.BICUBIC, expand=True,
                                                            fillcolor=255),
    "ruído forte (σ = 35)": _ruido(35),
    "desfoque (raio 2,5 px)": lambda imagem, rng: imagem.filter(ImageFilter.GaussianBlur(2.5)),
    "metade da resolução": lambda imagem, rng: imagem.resize((imagem.width // 2, imagem.height // 2),
                                                             Image.Resampling.LANCZOS),
    "um terço da resolução": lambda imagem, rng: imagem.resize((imagem.width // 3, imagem.height // 3),
                                                               Image.Resampling.LANCZOS),
    "JPEG qualidade 10": _jpeg(10),
}


def _imagem_do_arquivo(gabarito: dict) -> Image.Image:
    conteudo = (DOCUMENTOS / gabarito["arquivo"]).read_bytes()
    if gabarito["formato"] == "pdf_escaneado":
        return next(renderizar_paginas(conteudo, [0]))[1].convert("L")
    return abrir_imagem(conteudo).convert("L")


def estressar(extrator: Extrator) -> dict[str, list[ResultadoDocumento]]:
    """Lê de novo os documentos que passam por OCR depois de cada degradação."""
    cenario = carregar_cenario()
    escaneados = [g for g in cenario["documentos"] if g["formato"] in ("pdf_escaneado", "imagem_escaneada")]
    imagens = {g["id"]: _imagem_do_arquivo(g) for g in escaneados}
    resultados: dict[str, list[ResultadoDocumento]] = {}
    for nome, degradar in DEGRADACOES.items():
        rng = np.random.default_rng(SEMENTE_ESTRESSE)
        resultados[nome] = []
        for gabarito in escaneados:
            saida = io.BytesIO()
            degradar(imagens[gabarito["id"]], rng).save(saida, "PNG")
            resultados[nome].append(avaliar_arquivo(gabarito, saida.getvalue(), "degradado.png",
                                                    cenario["empresa"]["cnpj"], extrator))
    return resultados


# --------------------------------------------------------------------------- saída


def _pct(par: list[int]) -> str:
    acertos, total = par
    return f"{acertos}/{total} ({100 * acertos / total:.1f}%)" if total else "-"


def _tabela_matriz(resumo: dict) -> list[list[str]]:
    grupos = list(resumo["por_formato"])
    linhas = [["campo", *grupos, "total"]]
    for campo in CAMPOS:
        linhas.append([campo, *(_pct(resumo["matriz"][campo][g]) for g in grupos), _pct(resumo["por_campo"][campo])])
    linhas.append(["todos", *(_pct(resumo["por_formato"][g]) for g in grupos), _pct(resumo["acertos"])])
    return linhas


def _tabela_estresse(estresse: dict[str, list[ResultadoDocumento]]) -> list[list[str]]:
    ids = [r.id for r in next(iter(estresse.values()))]
    linhas = [["degradação", *ids, "campos certos", "valor e data certos", "erro sem alerta"]]
    for nome, resultados in estresse.items():
        celulas = [f"{r.acertos}/{len(CAMPOS)} · {r.confianca:.2f}" for r in resultados]
        campos = [sum(r.acertos for r in resultados), len(CAMPOS) * len(resultados)]
        valor_data = sum(r.valor_e_data_certos for r in resultados)
        silenciosos = sum(r.acertos < len(CAMPOS) and r.confianca >= CONFIANCA_REVISAO for r in resultados)
        linhas.append([nome, *celulas, _pct(campos), f"{valor_data}/{len(resultados)}", str(silenciosos)])
    return linhas


def _texto_alinhado(linhas: list[list[str]]) -> str:
    larguras = [max(len(linha[i]) for linha in linhas) for i in range(len(linhas[0]))]
    return "\n".join("  ".join(c.ljust(w) for c, w in zip(linha, larguras, strict=True)) for linha in linhas)


def _markdown(linhas: list[list[str]]) -> str:
    cabecalho, *corpo = linhas
    saida = ["| " + " | ".join(cabecalho) + " |", "|" + "|".join(" --- " for _ in cabecalho) + "|"]
    return "\n".join(saida + ["| " + " | ".join(linha) + " |" for linha in corpo])


def _versao_tesseract() -> str:
    try:
        return str(pytesseract.get_tesseract_version())
    except (OSError, pytesseract.TesseractNotFoundError):
        return "desconhecida"


def relatorio(resultados: list[ResultadoDocumento], resumo: dict, estresse: dict[str, list[ResultadoDocumento]],
              descricao_extrator: str) -> str:
    por_doc = [["documento", "formato", "acertos", "confiança", "problemas", "tempo (s)"]]
    for r in resultados:
        por_doc.append([f"{r.id} `{r.arquivo}`", r.grupo, f"{r.acertos}/{len(r.campos)}", f"{r.confianca:.2f}",
                        ", ".join(r.problemas) or "-", f"{r.segundos:.2f}"])
    erros = [f"- {e['documento']} · `{e['campo']}`: esperado `{e['esperado']}`, obtido `{e['obtido']}`"
             for e in resumo["erros"]]
    erros_estresse = [
        f"- {nome} · {r.id} · `{campo}`: esperado `{c.esperado}`, obtido `{c.obtido}`"
        for nome, lista in estresse.items() for r in lista for campo, c in r.campos.items() if not c.certo
    ]
    return "\n".join([
        "# Avaliação da leitura de documentos",
        "",
        "Gerado por `python scripts/avaliar_documentos.py`: os números abaixo foram medidos, não escritos à mão.",
        "",
        f"- Extrator: **{descricao_extrator}**",
        f"- Tesseract: {_versao_tesseract()} (`lang=por`)",
        f"- Documentos: {resumo['documentos']} arquivos de `dados/exemplos/documentos/`, gerados por "
        "`scripts/gerar_documentos.py` a partir de `dados/exemplos/cenario.json`",
        f"- Valor total **e** data de emissão certos: **{resumo['valor_e_data_emissao_certos']}**",
        f"- Acerto geral: **{_pct(resumo['acertos'])}** dos campos comparados",
        "",
        "## Acerto por campo e por formato",
        "",
        _markdown(_tabela_matriz(resumo)),
        "",
        f"`emitente_nome` conta como certo com similaridade ≥ {SIMILARIDADE_MINIMA_NOME} (rapidfuzz "
        "`token_sort_ratio`, sem diferenciar maiúsculas e pontuação). Os outros campos são comparados exatamente; "
        "campo que o gabarito deixa vazio (ex.: CNPJ de recibo de pessoa física) só conta como certo se a leitura "
        "também deixar vazio.",
        "",
        "## Por documento",
        "",
        _markdown(por_doc),
        "",
        "## Erros",
        "",
        *(erros or ["Nenhum campo errado nesta execução."]),
        "",
        "## Teste de estresse do OCR",
        "",
        "Os documentos que passam por OCR, piorados de propósito antes da leitura (uma degradação por vez, por cima "
        "do arquivo versionado, que já vem torto, ruidoso e borrado). Cada célula mostra quantos dos "
        f"{len(CAMPOS)} campos saíram certos e a confiança calculada. **Erro sem alerta** conta os documentos com "
        f"algum campo errado e confiança ≥ {CONFIANCA_REVISAO}, ou seja, que passariam sem revisão: é o número que "
        "importa, porque erro com confiança baixa vai para uma pessoa conferir.",
        "",
        _markdown(_tabela_estresse(estresse)),
        "",
        *(["Campos errados no estresse:", "", *erros_estresse] if erros_estresse else
          ["Nenhum campo errado no estresse."]),
        "",
        "## Como ler estes números",
        "",
        "Os 12 arquivos são sintéticos: o mesmo gerador desenhou o layout e o leitor foi escrito conhecendo esse "
        "layout. Acerto alto aqui mostra que o caminho inteiro funciona (detecção de formato, XML, camada de texto, "
        "OCR com pré-processamento, regras e confiança), não que a taxa vale para documentos reais, que variam "
        "muito mais de layout e de qualidade de imagem. O teste de estresse mede só a imagem, com o layout fixo. "
        "Para medir de verdade, rode o mesmo script sobre uma pasta de documentos reais anotados.",
        "",
    ])


def main() -> None:
    parser = argparse.ArgumentParser(description="Avalia a leitura dos documentos de exemplo contra o gabarito.")
    parser.add_argument("--extrator", choices=("regras", "llm"), default="regras")
    parser.add_argument("--saida", type=Path, default=RELATORIO, help="arquivo .md do relatório")
    argumentos = parser.parse_args()

    extrator: Extrator = ExtratorLLM() if argumentos.extrator == "llm" else ExtratorRegras()
    descricao = f"llm ({extrator.modelo})" if isinstance(extrator, ExtratorLLM) else "regras"
    resultados = avaliar_detalhado(extrator)
    resumo = resumir(resultados)
    estresse = estressar(extrator)

    print(f"Extrator: {descricao}\n")
    print(_texto_alinhado(_tabela_matriz(resumo)))
    print(f"\nValor total e data de emissão certos: {resumo['valor_e_data_emissao_certos']}")
    for erro in resumo["erros"]:
        print(f"ERRO {erro['documento']} {erro['campo']}: esperado {erro['esperado']!r}, obtido {erro['obtido']!r}")
    print("\nTeste de estresse do OCR\n")
    print(_texto_alinhado(_tabela_estresse(estresse)))

    argumentos.saida.parent.mkdir(parents=True, exist_ok=True)
    argumentos.saida.write_text(relatorio(resultados, resumo, estresse, descricao), encoding="utf-8")
    print(f"\nRelatório gravado em {argumentos.saida}")


if __name__ == "__main__":
    main()
