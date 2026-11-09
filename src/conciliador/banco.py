"""Persistência com SQLAlchemy. SQLite por padrão; PostgreSQL com DATABASE_URL=postgresql+psycopg://...

Os objetos do contrato são guardados como JSON (Pydantic serializa Decimal e date sem perda). O que a tela
filtra ou ordena (status, data) vira coluna própria.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime

from sqlalchemy import JSON, DateTime, Integer, LargeBinary, String, create_engine, select
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

from conciliador.modelos import DocumentoExtraido, Lancamento, ResultadoConciliacao, StatusLancamento, TransacaoBancaria


class Base(DeclarativeBase):
    pass


def _agora() -> datetime:
    return datetime.now(UTC)


class DocumentoRow(Base):
    __tablename__ = "documentos"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    nome_arquivo: Mapped[str] = mapped_column(String(255))
    tipo_mime: Mapped[str] = mapped_column(String(80))
    arquivo: Mapped[bytes] = mapped_column(LargeBinary)
    dados: Mapped[dict] = mapped_column(JSON)
    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_agora)


class TransacaoRow(Base):
    __tablename__ = "transacoes"
    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    extrato: Mapped[str] = mapped_column(String(255))
    dados: Mapped[dict] = mapped_column(JSON)


class LancamentoRow(Base):
    __tablename__ = "lancamentos"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    chave: Mapped[str] = mapped_column(String(200), unique=True)  # evita duplicar ao reprocessar
    status: Mapped[str] = mapped_column(String(20), index=True)
    dados: Mapped[dict] = mapped_column(JSON)
    atualizado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_agora, onupdate=_agora)


class ConciliacaoRow(Base):
    __tablename__ = "conciliacao"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    dados: Mapped[dict] = mapped_column(JSON)


def chave_do_lancamento(lanc: Lancamento) -> str:
    """Mesmo fato contábil, mesma chave: registro do doc, baixa doc+transação ou transação avulsa."""
    if lanc.documento_id and lanc.transacao_id:
        return f"baixa:{lanc.documento_id}:{lanc.transacao_id}"
    if lanc.documento_id:
        return f"registro:{lanc.documento_id}"
    return f"avulso:{lanc.transacao_id}"


class Banco:
    def __init__(self, url: str | None = None):
        url = url or os.environ.get("DATABASE_URL", "sqlite:///conciliador.db")
        argumentos = {"check_same_thread": False} if url.startswith("sqlite") else {}
        self.engine = create_engine(url, connect_args=argumentos)
        Base.metadata.create_all(self.engine)
        self._sessao = sessionmaker(self.engine, expire_on_commit=False)

    def sessao(self) -> Session:
        return self._sessao()

    # documentos
    def salvar_documento(self, doc_id: str, nome: str, mime: str, arquivo: bytes, doc: DocumentoExtraido) -> None:
        with self.sessao() as s, s.begin():
            s.merge(DocumentoRow(id=doc_id, nome_arquivo=nome, tipo_mime=mime, arquivo=arquivo,
                                 dados=doc.model_dump(mode="json")))

    def documentos(self) -> dict[str, DocumentoExtraido]:
        with self.sessao() as s:
            linhas = s.scalars(select(DocumentoRow).order_by(DocumentoRow.id)).all()
            return {r.id: DocumentoExtraido.model_validate(r.dados) for r in linhas}

    def documento_info(self) -> dict[str, str]:
        with self.sessao() as s:
            return {r.id: r.nome_arquivo for r in s.scalars(select(DocumentoRow))}

    def arquivo(self, doc_id: str) -> tuple[str, str, bytes] | None:
        with self.sessao() as s:
            r = s.get(DocumentoRow, doc_id)
            return (r.nome_arquivo, r.tipo_mime, r.arquivo) if r else None

    # transações
    def salvar_transacoes(self, extrato: str, transacoes: list[TransacaoBancaria]) -> int:
        """Importar o mesmo extrato duas vezes não duplica: o id da transação é estável."""
        with self.sessao() as s, s.begin():
            novas = 0
            for t in transacoes:
                if s.get(TransacaoRow, t.id) is None:
                    novas += 1
                s.merge(TransacaoRow(id=t.id, extrato=extrato, dados=t.model_dump(mode="json")))
            return novas

    def transacoes(self) -> list[TransacaoBancaria]:
        with self.sessao() as s:
            ts = [TransacaoBancaria.model_validate(r.dados) for r in s.scalars(select(TransacaoRow))]
            return sorted(ts, key=lambda t: (t.data, t.id))

    # conciliação
    def salvar_conciliacao(self, resultado: ResultadoConciliacao) -> None:
        with self.sessao() as s, s.begin():
            s.merge(ConciliacaoRow(id=1, dados=resultado.model_dump(mode="json")))

    def conciliacao(self) -> ResultadoConciliacao | None:
        with self.sessao() as s:
            r = s.get(ConciliacaoRow, 1)
            return ResultadoConciliacao.model_validate(r.dados) if r else None

    # lançamentos
    def substituir_sugestoes(self, lancamentos: list[Lancamento]) -> None:
        """Reprocessar troca só o que ainda é sugestão; o que alguém aprovou ou rejeitou fica."""
        with self.sessao() as s, s.begin():
            decididos = set(
                s.scalars(select(LancamentoRow.chave).where(LancamentoRow.status != StatusLancamento.SUGERIDO))
            )
            for r in s.scalars(select(LancamentoRow).where(LancamentoRow.status == StatusLancamento.SUGERIDO)):
                s.delete(r)
            s.flush()
            for lanc in lancamentos:
                chave = chave_do_lancamento(lanc)
                if chave not in decididos:
                    s.add(LancamentoRow(chave=chave, status=lanc.status, dados=lanc.model_dump(mode="json")))

    def lancamentos(self, status: str | None = None) -> list[tuple[int, Lancamento]]:
        with self.sessao() as s:
            consulta = select(LancamentoRow)
            if status:
                consulta = consulta.where(LancamentoRow.status == status)
            linhas = s.scalars(consulta).all()
            itens = [(r.id, Lancamento.model_validate(r.dados)) for r in linhas]
            return sorted(itens, key=lambda par: (par[1].data, par[1].historico))

    def atualizar_lancamento(self, lanc_id: int, lanc: Lancamento) -> bool:
        with self.sessao() as s, s.begin():
            r = s.get(LancamentoRow, lanc_id)
            if r is None:
                return False
            r.status = lanc.status
            r.dados = lanc.model_dump(mode="json")
            return True

    def lancamento(self, lanc_id: int) -> Lancamento | None:
        with self.sessao() as s:
            r = s.get(LancamentoRow, lanc_id)
            return Lancamento.model_validate(r.dados) if r else None
