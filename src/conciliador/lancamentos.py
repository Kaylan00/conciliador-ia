"""Classificação no plano de contas e lançamentos em partidas dobradas.

Fluxo de um documento de compra pago pelo banco (regime de competência):
  1. registro, na data de emissão:  D despesa ou custo   / C fornecedores (ou contas a pagar)
  2. baixa, na data do extrato:      D fornecedores       / C banco
Venda recebida é o espelho: D clientes / C receita, e depois D banco / C clientes.
Transação sem documento (tarifa, rendimento) vira um lançamento direto contra o banco.

Classificação: regras primeiro (palavras-chave do plano, explicáveis e de graça); o modelo de linguagem
só entra no que as regras não resolveram, e a resposta dele só vale se for uma conta folha permitida.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal

from conciliador import plano_contas as pc
from conciliador.modelos import (
    Direcao,
    DocumentoExtraido,
    Lancamento,
    Partida,
    ResultadoConciliacao,
    TipoDocumento,
    TransacaoBancaria,
)
from conciliador.plano_contas import PlanoDeContas, plano_padrao


@dataclass(frozen=True)
class Classificacao:
    conta: str
    justificativa: str
    confianca: float  # 1.0 regra inequívoca, 0.6 empate ou padrão por tipo, 0.0 precisa de alguém
    fonte: str  # "regras", "tipo", "modelo" ou "sem_classificacao"


# Recebe (texto do documento, contas permitidas) e devolve (código, justificativa) ou None
ClassificadorModelo = Callable[[str, list[pc.Conta]], tuple[str, str] | None]


def normalizar(texto: str) -> str:
    sem_acento = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode()
    return re.sub(r"\s+", " ", sem_acento.lower()).strip()


def _contas_permitidas(plano: PlanoDeContas, receita: bool) -> list[pc.Conta]:
    grupo = "3" if receita else "4"
    return [c for c in plano.folhas_do_grupo(grupo) if c.codigo != pc.A_CLASSIFICAR]


def _por_palavras(texto: str, contas: list[pc.Conta]) -> list[tuple[pc.Conta, list[str]]]:
    alvo = normalizar(texto)
    achados = []
    for conta in contas:
        palavras = [p for p in conta.palavras if re.search(rf"\b{re.escape(normalizar(p))}", alvo)]
        if palavras:
            achados.append((conta, palavras))
    achados.sort(key=lambda par: len(par[1]), reverse=True)
    return achados


def texto_do_documento(doc: DocumentoExtraido) -> str:
    partes = [doc.descricao or "", *(i.descricao for i in doc.itens), doc.contraparte_nome or ""]
    return " | ".join(p for p in partes if p)


def classificar_documento(
    doc: DocumentoExtraido,
    plano: PlanoDeContas | None = None,
    modelo: ClassificadorModelo | None = None,
) -> Classificacao:
    plano = plano or plano_padrao()
    receita = doc.direcao == Direcao.RECEBER
    contas = _contas_permitidas(plano, receita)
    texto = texto_do_documento(doc)

    achados = _por_palavras(texto, contas)
    if achados:
        conta, palavras = achados[0]
        empate = len(achados) > 1 and len(achados[1][1]) == len(palavras)
        motivo = f"palavras do plano encontradas: {', '.join(palavras)}"
        if empate:
            motivo += f" (empatou com {achados[1][0].codigo} {achados[1][0].nome})"
        return Classificacao(conta.codigo, motivo, 0.6 if empate else 1.0, "regras")

    if receita and doc.tipo in (TipoDocumento.NFE, TipoDocumento.NFSE):
        codigo = "3.1.2" if doc.tipo == TipoDocumento.NFSE else "3.1.1"
        return Classificacao(codigo, f"{doc.tipo.value.upper()} emitida pela empresa", 0.6, "tipo")

    if modelo is not None:
        resposta = modelo(texto, contas)
        if resposta is not None:
            codigo, justificativa = resposta
            if codigo in {c.codigo for c in contas}:
                return Classificacao(codigo, f"sugestão do modelo: {justificativa}", 0.7, "modelo")

    padrao = "3.1.1" if receita else pc.A_CLASSIFICAR
    return Classificacao(padrao, "nenhuma regra bateu: precisa de alguém classificar", 0.0, "sem_classificacao")


def classificar_transacao(t: TransacaoBancaria, plano: PlanoDeContas | None = None) -> Classificacao:
    """Para transação sem documento: tarifa, IOF, rendimento. O resto fica para a pessoa decidir."""
    plano = plano or plano_padrao()
    receita = t.valor > 0
    achados = _por_palavras(t.descricao, _contas_permitidas(plano, receita))
    if achados:
        conta, palavras = achados[0]
        return Classificacao(conta.codigo, f"memo do banco cita: {', '.join(palavras)}", 1.0, "regras")
    padrao = "3.1.1" if receita else pc.A_CLASSIFICAR
    return Classificacao(padrao, "transação sem documento: peça a nota ou classifique", 0.0, "sem_classificacao")


def _passivo(doc: DocumentoExtraido) -> str:
    return pc.FORNECEDORES if doc.tipo in (TipoDocumento.NFE, TipoDocumento.NFSE) else pc.CONTAS_A_PAGAR


def _historico(doc: DocumentoExtraido) -> str:
    numero = f" {doc.numero}" if doc.numero else ""
    quem = doc.contraparte_nome or "sem nome"
    return f"{doc.tipo.value.upper()}{numero} {quem}"


def lancamento_de_registro(doc_id: str, doc: DocumentoExtraido, cls: Classificacao) -> Lancamento | None:
    if doc.valor_total is None or doc.data_emissao is None:
        return None
    valor = doc.valor_total
    if doc.direcao == Direcao.PAGAR:
        partidas = [Partida(conta=cls.conta, debito=valor), Partida(conta=_passivo(doc), credito=valor)]
    else:
        partidas = [Partida(conta=pc.CLIENTES, debito=valor), Partida(conta=cls.conta, credito=valor)]
    return Lancamento(
        data=doc.data_emissao,
        historico=f"Registro {_historico(doc)}",
        partidas=partidas,
        documento_id=doc_id,
        justificativa=cls.justificativa,
    )


def lancamento_de_baixa(doc_id: str, doc: DocumentoExtraido, t: TransacaoBancaria, motivos: list[str]) -> Lancamento:
    valor = abs(t.valor)
    if doc.direcao == Direcao.PAGAR:
        partidas = [Partida(conta=_passivo(doc), debito=valor), Partida(conta=pc.BANCO, credito=valor)]
        acao = "Pagamento"
    else:
        partidas = [Partida(conta=pc.BANCO, debito=valor), Partida(conta=pc.CLIENTES, credito=valor)]
        acao = "Recebimento"
    return Lancamento(
        data=t.data,
        historico=f"{acao} {_historico(doc)}",
        partidas=partidas,
        documento_id=doc_id,
        transacao_id=t.id,
        justificativa="conciliado com o extrato: " + "; ".join(motivos),
    )


def lancamento_avulso(t: TransacaoBancaria, cls: Classificacao) -> Lancamento:
    valor = abs(t.valor)
    if t.valor < 0:
        partidas = [Partida(conta=cls.conta, debito=valor), Partida(conta=pc.BANCO, credito=valor)]
    else:
        partidas = [Partida(conta=pc.BANCO, debito=valor), Partida(conta=cls.conta, credito=valor)]
    return Lancamento(
        data=t.data,
        historico=t.descricao,
        partidas=partidas,
        transacao_id=t.id,
        justificativa=cls.justificativa,
    )


@dataclass
class Sugestoes:
    lancamentos: list[Lancamento]
    classificacoes: dict[str, Classificacao]  # por documento_id e por transacao_id (avulsas)


def sugerir_lancamentos(
    documentos: dict[str, DocumentoExtraido],
    transacoes: list[TransacaoBancaria],
    conciliacao: ResultadoConciliacao,
    plano: PlanoDeContas | None = None,
    modelo: ClassificadorModelo | None = None,
) -> Sugestoes:
    plano = plano or plano_padrao()
    por_id = {t.id: t for t in transacoes}
    lancamentos: list[Lancamento] = []
    classificacoes: dict[str, Classificacao] = {}

    for doc_id, doc in documentos.items():
        cls = classificar_documento(doc, plano, modelo)
        classificacoes[doc_id] = cls
        registro = lancamento_de_registro(doc_id, doc, cls)
        if registro is not None:
            lancamentos.append(registro)

    for par in conciliacao.pares:
        doc = documentos[par.documento_id]
        lancamentos.append(lancamento_de_baixa(par.documento_id, doc, por_id[par.transacao_id], par.motivos))

    for t_id in conciliacao.transacoes_sem_documento:
        t = por_id[t_id]
        cls = classificar_transacao(t, plano)
        classificacoes[t_id] = cls
        lancamentos.append(lancamento_avulso(t, cls))

    lancamentos.sort(key=lambda lanc: (lanc.data, lanc.historico))
    return Sugestoes(lancamentos, classificacoes)


@dataclass(frozen=True)
class LinhaBalancete:
    conta: str
    nome: str
    debitos: Decimal
    creditos: Decimal
    saldo: Decimal  # positivo na natureza da conta


def balancete(lancamentos: list[Lancamento], plano: PlanoDeContas | None = None) -> list[LinhaBalancete]:
    plano = plano or plano_padrao()
    debitos: dict[str, Decimal] = {}
    creditos: dict[str, Decimal] = {}
    for lanc in lancamentos:
        for p in lanc.partidas:
            debitos[p.conta] = debitos.get(p.conta, Decimal("0")) + p.debito
            creditos[p.conta] = creditos.get(p.conta, Decimal("0")) + p.credito
    linhas = []
    for codigo in sorted(set(debitos) | set(creditos), key=lambda c: [int(x) for x in c.split(".")]):
        conta = plano[codigo]
        d, c = debitos.get(codigo, Decimal("0")), creditos.get(codigo, Decimal("0"))
        saldo = d - c if conta.natureza == "devedora" else c - d
        linhas.append(LinhaBalancete(codigo, conta.nome, d, c, saldo))
    return linhas
