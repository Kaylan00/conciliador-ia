"""API e tela de revisão.

uvicorn conciliador.api:app --reload    ->  http://localhost:8000 (tela) e /docs (Swagger)

As dependências pesadas (OCR, leitura de extrato, conciliação) entram por injeção em `criar_app`, para os
testes da API rodarem sem Tesseract e para cada peça poder ser trocada sem mexer nas rotas.
"""

from __future__ import annotations

import hashlib
import mimetypes
import os
from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, Response
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from conciliador import plano_contas as pc
from conciliador.banco import Banco
from conciliador.lancamentos import ClassificadorModelo, balancete, sugerir_lancamentos
from conciliador.modelos import DocumentoExtraido, ResultadoConciliacao, StatusLancamento, TransacaoBancaria

RAIZ = Path(__file__).resolve().parents[2]
EXEMPLOS = RAIZ / "dados" / "exemplos"
CONTAS_DE_CONTROLE = {pc.BANCO, pc.CLIENTES, pc.FORNECEDORES, pc.CONTAS_A_PAGAR}
CONFIANCA_MINIMA = 0.8  # abaixo disso o documento aparece nas pendências

LeitorDocumento = Callable[[bytes, str, str | None], DocumentoExtraido]
LeitorExtrato = Callable[[bytes, str], list[TransacaoBancaria]]
Conciliador = Callable[[dict[str, DocumentoExtraido], list[TransacaoBancaria]], ResultadoConciliacao]


def _ler_documento(conteudo: bytes, nome: str, cnpj_empresa: str | None) -> DocumentoExtraido:
    from conciliador.documentos import ler_documento

    return ler_documento(conteudo, nome, cnpj_empresa=cnpj_empresa)


def _ler_extrato(conteudo: bytes, nome: str) -> list[TransacaoBancaria]:
    from conciliador.extrato import ler_extrato

    return ler_extrato(conteudo, nome)


def _conciliar(docs: dict[str, DocumentoExtraido], ts: list[TransacaoBancaria]) -> ResultadoConciliacao:
    from conciliador.conciliacao import conciliar

    return conciliar(docs, ts)


@dataclass
class Dependencias:
    banco: Banco
    ler_documento: LeitorDocumento = _ler_documento
    ler_extrato: LeitorExtrato = _ler_extrato
    conciliar: Conciliador = _conciliar
    modelo: ClassificadorModelo | None = None
    cnpj_empresa: str | None = None


class TrocaDeConta(BaseModel):
    conta: str


def id_do_documento(conteudo: bytes) -> str:
    """O mesmo arquivo enviado duas vezes vira o mesmo documento."""
    return "doc-" + hashlib.sha256(conteudo).hexdigest()[:10]


def criar_app(dep: Dependencias) -> FastAPI:
    app = FastAPI(title="Conciliador IA", description="Documentos fiscais, extrato, conciliação e lançamentos")
    templates = Jinja2Templates(directory=Path(__file__).parent / "web" / "templates")
    plano = pc.plano_padrao()

    def importar_documento(nome: str, conteudo: bytes) -> dict:
        doc = dep.ler_documento(conteudo, nome, dep.cnpj_empresa)
        doc_id = id_do_documento(conteudo)
        mime = mimetypes.guess_type(nome)[0] or "application/octet-stream"
        dep.banco.salvar_documento(doc_id, nome, mime, conteudo, doc)
        return {"id": doc_id, "arquivo": nome, "confianca": doc.confianca, "problemas": len(doc.problemas)}

    def processar() -> dict:
        docs, ts = dep.banco.documentos(), dep.banco.transacoes()
        resultado = dep.conciliar(docs, ts)
        dep.banco.salvar_conciliacao(resultado)
        sugestoes = sugerir_lancamentos(docs, ts, resultado, plano, dep.modelo)
        dep.banco.substituir_sugestoes(sugestoes.lancamentos)
        return {
            "documentos": len(docs),
            "transacoes": len(ts),
            "pares": len(resultado.pares),
            "transacoes_sem_documento": len(resultado.transacoes_sem_documento),
            "documentos_sem_transacao": len(resultado.documentos_sem_transacao),
            "lancamentos_sugeridos": len(sugestoes.lancamentos),
        }

    @app.get("/saude")
    def saude() -> dict:
        return {"status": "ok"}

    @app.post("/api/documentos")
    async def enviar_documentos(arquivos: list[UploadFile]) -> list[dict]:
        return [importar_documento(a.filename or "sem_nome", await a.read()) for a in arquivos]

    @app.get("/api/documentos")
    def listar_documentos() -> dict[str, DocumentoExtraido]:
        return dep.banco.documentos()

    @app.get("/api/documentos/{doc_id}/arquivo")
    def arquivo_original(doc_id: str) -> Response:
        achado = dep.banco.arquivo(doc_id)
        if achado is None:
            raise HTTPException(404, "documento não encontrado")
        nome, mime, conteudo = achado
        return Response(conteudo, media_type=mime, headers={"Content-Disposition": f'inline; filename="{nome}"'})

    @app.post("/api/extratos")
    async def enviar_extrato(arquivo: UploadFile) -> dict:
        nome = arquivo.filename or "extrato"
        try:
            transacoes = dep.ler_extrato(await arquivo.read(), nome)
        except ValueError as erro:
            raise HTTPException(422, str(erro)) from erro
        return {"transacoes": len(transacoes), "novas": dep.banco.salvar_transacoes(nome, transacoes)}

    @app.post("/api/processar")
    def rodar_processamento() -> dict:
        return processar()

    @app.post("/api/exemplo")
    def carregar_exemplo() -> dict:
        """Lê os arquivos de dados/exemplos como se alguém tivesse enviado, e processa."""
        pasta = EXEMPLOS / "documentos"
        for caminho in sorted(pasta.glob("*")) if pasta.exists() else []:
            importar_documento(caminho.name, caminho.read_bytes())
        extrato = EXEMPLOS / "extrato_outubro.ofx"
        if extrato.exists():
            dep.banco.salvar_transacoes(extrato.name, dep.ler_extrato(extrato.read_bytes(), extrato.name))
        return processar()

    @app.get("/api/lancamentos")
    def listar_lancamentos(status: StatusLancamento | None = None) -> list[dict]:
        return [{"id": i, **lanc.model_dump(mode="json")} for i, lanc in dep.banco.lancamentos(status)]

    def _mudar_status(lanc_id: int, status: StatusLancamento) -> dict:
        lanc = dep.banco.lancamento(lanc_id)
        if lanc is None:
            raise HTTPException(404, "lançamento não encontrado")
        if status == StatusLancamento.APROVADO:
            if not lanc.fechado:
                raise HTTPException(422, "débitos e créditos não batem")
            if any(p.conta == pc.A_CLASSIFICAR for p in lanc.partidas):
                raise HTTPException(422, "escolha a conta antes de aprovar")
        lanc.status = status
        dep.banco.atualizar_lancamento(lanc_id, lanc)
        return {"id": lanc_id, "status": status}

    @app.post("/api/lancamentos/{lanc_id}/aprovar")
    def aprovar(lanc_id: int) -> dict:
        return _mudar_status(lanc_id, StatusLancamento.APROVADO)

    @app.post("/api/lancamentos/{lanc_id}/rejeitar")
    def rejeitar(lanc_id: int) -> dict:
        return _mudar_status(lanc_id, StatusLancamento.REJEITADO)

    @app.put("/api/lancamentos/{lanc_id}/conta")
    def trocar_conta(lanc_id: int, troca: TrocaDeConta) -> dict:
        lanc = dep.banco.lancamento(lanc_id)
        if lanc is None:
            raise HTTPException(404, "lançamento não encontrado")
        if not plano.aceita_lancamento(troca.conta) or troca.conta in CONTAS_DE_CONTROLE:
            raise HTTPException(422, "conta inválida: use uma conta folha de receita ou despesa")
        partida = next((p for p in lanc.partidas if p.conta not in CONTAS_DE_CONTROLE), None)
        if partida is None:
            raise HTTPException(422, "este lançamento só mexe em contas de controle")
        anterior, partida.conta = partida.conta, troca.conta
        lanc.justificativa = f"conta trocada na revisão: {anterior} -> {troca.conta}"
        dep.banco.atualizar_lancamento(lanc_id, lanc)
        return {"id": lanc_id, "conta": troca.conta}

    @app.get("/api/balancete")
    def ver_balancete(somente_aprovados: bool = False) -> list[dict]:
        status = StatusLancamento.APROVADO if somente_aprovados else None
        lancs = [lanc for _, lanc in dep.banco.lancamentos(status) if lanc.status != StatusLancamento.REJEITADO]
        return [linha.__dict__ for linha in balancete(lancs, plano)]

    @app.get("/", response_class=HTMLResponse)
    def tela(request: Request) -> HTMLResponse:
        docs = dep.banco.documentos()
        nomes = dep.banco.documento_info()
        conciliacao = dep.banco.conciliacao()
        transacoes = {t.id: t for t in dep.banco.transacoes()}
        lancamentos = dep.banco.lancamentos()
        validos = [lanc for _, lanc in lancamentos if lanc.status != StatusLancamento.REJEITADO]
        pendentes_docs = {i: d for i, d in docs.items() if d.confianca < CONFIANCA_MINIMA}
        return templates.TemplateResponse(
            request,
            "revisao.html",
            {
                "docs": docs,
                "nomes": nomes,
                "pendentes_docs": pendentes_docs,
                "conciliacao": conciliacao,
                "transacoes": transacoes,
                "lancamentos": lancamentos,
                "n_aprovados": sum(1 for _, lanc in lancamentos if lanc.status == StatusLancamento.APROVADO),
                "balancete": balancete(validos, plano),
                "plano": plano,
                "contas_editaveis": [c for c in plano.folhas if c.codigo not in CONTAS_DE_CONTROLE],
                "controle": CONTAS_DE_CONTROLE,
                "a_classificar": pc.A_CLASSIFICAR,
                "minimo": CONFIANCA_MINIMA,
                "brl": brl,
            },
        )

    return app


def brl(valor: Decimal | float | None) -> str:
    if valor is None:
        return "-"
    texto = f"{Decimal(valor):,.2f}"
    return "R$ " + texto.replace(",", "X").replace(".", ",").replace("X", ".")


def app_padrao() -> FastAPI:
    from conciliador.classificador_llm import ClassificadorLLM

    return criar_app(
        Dependencias(
            banco=Banco(),
            modelo=ClassificadorLLM.do_ambiente(),
            cnpj_empresa=os.environ.get("EMPRESA_CNPJ", "52601815000120"),
        )
    )


def __getattr__(nome: str):  # `uvicorn conciliador.api:app` cria o app só quando alguém pede
    if nome == "app":
        return app_padrao()
    raise AttributeError(nome)
