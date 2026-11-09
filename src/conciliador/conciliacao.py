"""Conciliação: casa cada transação do extrato com o documento que ela paga ou recebe.

Um par só é candidato quando o valor bate (tolerância de R$ 0,01) e o sinal respeita a direção do documento:
a pagar sai da conta (transação negativa), a receber entra (positiva). Entre os candidatos, o score em [0, 1] é

    score = PESO_DATA * data + PESO_NOME * nome + BONUS_CNPJ (se o CNPJ aparece no memo), limitado a 1

- data: no dia do vencimento (ou da emissão, se não houver vencimento) vale 1, e perde 5% por dia de distância,
  para frente ou para trás, até zerar em 20 dias. Transação anterior à emissão quase sempre é outra coisa: a data
  zera e o score inteiro cai pela metade.
- nome: semelhança (rapidfuzz WRatio) entre o nome da contraparte e o memo, os dois sem acento, sem sufixo de
  empresa (LTDA, S.A., ME...) e o memo sem o ruído do banco (PIX ENVIADO, PAG BOLETO, TED...). Até 50% é o que
  dois nomes sem relação nenhuma já marcam, então vale 0; de 50% a 100% a nota sobe de 0 a 1. Memo sem nome
  nenhum vale 0,5: não confirma nem desmente.

A atribuição é global (algoritmo húngaro sobre 1 - score), não gulosa: dois boletos iguais do mesmo fornecedor
vão cada um para o pagamento da sua data, e um documento não "rouba" a transação que só servia para outro.
"""

from __future__ import annotations

import re
import unicodedata
from datetime import date
from decimal import Decimal

import numpy as np
from pydantic import BaseModel, Field
from rapidfuzz import fuzz
from scipy.optimize import linear_sum_assignment

from conciliador.modelos import Direcao, DocumentoExtraido, ParConciliado, ResultadoConciliacao, TransacaoBancaria

TOLERANCIA_VALOR = Decimal("0.01")
PESO_DATA = 0.5
PESO_NOME = 0.5
BONUS_CNPJ = 0.2
DIAS_PARA_ZERAR_A_DATA = 20
FATOR_ANTES_DA_EMISSAO = 0.5
PISO_DE_SIMILARIDADE = 0.5
SIMILARIDADE_DE_NOME_PARECIDO = 0.8
NOTA_NEUTRA = 0.5

_CUSTO_INVIAVEL = 1e6
# Palavras que o banco acrescenta ao memo e que não identificam ninguém ("PAGAMENTO DE TITULO", "PIX ENVIADO").
_PALAVRAS_DO_BANCO = frozenset(
    "PIX TED DOC TEF TRANSF TRANSFERENCIA ENVIADO ENVIADA RECEBIDO RECEBIDA PAG PAGTO PAGAMENTO BOLETO TITULO "
    "CONTA DEB DEBITO AUT AUTOMATICO COMPRA CARTAO CRED CREDITO DE DA DO DAS DOS".split()
)
_SUFIXO_DE_EMPRESA = re.compile(r"(?:\s+(?:LTDA|S A|SA|ME|EPP|EIRELI|MEI))+$")
_NUMERO_COM_PONTUACAO = re.compile(r"\d[\d./-]*\d")
_PONTO_POR_VIRGULA = str.maketrans(",.", ".,")


class Candidato(BaseModel):
    """Transação que pode ser o pagamento (ou recebimento) de um documento, com o score e os motivos."""

    transacao: TransacaoBancaria
    score: float = Field(ge=0, le=1)
    motivos: list[str]


def candidatos(documento: DocumentoExtraido, transacoes: list[TransacaoBancaria]) -> list[Candidato]:
    """Transações com valor e sentido compatíveis com o documento, da mais provável para a menos provável.

    Não aplica o score mínimo: a tela de revisão mostra as alternativas mesmo quando nenhuma convence.
    """
    avaliados = (_avaliar(documento, transacao) for transacao in transacoes)
    return sorted((c for c in avaliados if c is not None), key=lambda c: c.score, reverse=True)


def conciliar(
    documentos: dict[str, DocumentoExtraido],
    transacoes: list[TransacaoBancaria],
    score_minimo: float = 0.55,
) -> ResultadoConciliacao:
    """Casa documentos e transações um para um, maximizando o score total.

    Par abaixo de `score_minimo` não entra: o documento e a transação vão para as sobras, para uma pessoa decidir.
    Os pares saem na ordem do extrato.
    """
    ids = list(documentos)
    aceitos: dict[tuple[int, int], Candidato] = {}
    for i, documento_id in enumerate(ids):
        for j, transacao in enumerate(transacoes):
            candidato = _avaliar(documentos[documento_id], transacao)
            if candidato is not None and candidato.score >= score_minimo:
                aceitos[i, j] = candidato

    escolhidos: list[tuple[int, int]] = []
    if aceitos:
        custo = np.full((len(ids), len(transacoes)), _CUSTO_INVIAVEL)
        for (i, j), candidato in aceitos.items():
            custo[i, j] = 1 - candidato.score
        # O solver sempre preenche min(linhas, colunas) pares; os inviáveis que ele usou para completar são descartados.
        linhas, colunas = linear_sum_assignment(custo)
        escolhidos = sorted((j, i) for i, j in zip(linhas, colunas, strict=True) if (i, j) in aceitos)

    pares = [
        ParConciliado(
            transacao_id=transacoes[j].id,
            documento_id=ids[i],
            score=round(aceitos[i, j].score, 3),
            motivos=aceitos[i, j].motivos,
        )
        for j, i in escolhidos
    ]
    transacoes_usadas = {j for j, _ in escolhidos}
    documentos_usados = {i for _, i in escolhidos}
    return ResultadoConciliacao(
        pares=pares,
        transacoes_sem_documento=[t.id for j, t in enumerate(transacoes) if j not in transacoes_usadas],
        documentos_sem_transacao=[d for i, d in enumerate(ids) if i not in documentos_usados],
    )


def _avaliar(documento: DocumentoExtraido, transacao: TransacaoBancaria) -> Candidato | None:
    """Score e motivos do par, ou None quando valor ou sentido não batem e o par nem é candidato."""
    if documento.valor_total is None or not _sentido_confere(documento.direcao, transacao.valor):
        return None
    diferenca = abs(abs(transacao.valor) - documento.valor_total)
    if diferenca > TOLERANCIA_VALOR:
        return None

    nota_data, motivo_data = _nota_data(documento, transacao.data)
    nota_nome, motivo_nome = _nota_nome(documento.contraparte_nome, transacao.descricao)
    score = PESO_DATA * nota_data + PESO_NOME * nota_nome
    motivos = [_motivo_valor(documento.valor_total, transacao.valor, diferenca), motivo_data, motivo_nome]
    if _cnpj_no_memo(documento.contraparte_cnpj, transacao.descricao):
        score += BONUS_CNPJ
        motivos.append("CNPJ da contraparte aparece no memo")
    if _antes_da_emissao(documento, transacao.data):
        score *= FATOR_ANTES_DA_EMISSAO
    return Candidato(transacao=transacao, score=min(score, 1.0), motivos=motivos)


def _sentido_confere(direcao: Direcao, valor: Decimal) -> bool:
    return valor < 0 if direcao == Direcao.PAGAR else valor > 0


def _antes_da_emissao(documento: DocumentoExtraido, dia: date) -> bool:
    return documento.data_emissao is not None and dia < documento.data_emissao


def _nota_data(documento: DocumentoExtraido, dia: date) -> tuple[float, str]:
    verbo = "pago" if documento.direcao == Direcao.PAGAR else "recebido"
    if _antes_da_emissao(documento, dia):
        return 0.0, f"{verbo} {_dias((documento.data_emissao - dia).days)} antes da emissão do documento"
    if documento.data_vencimento is not None:
        referencia, qual = documento.data_vencimento, "do vencimento"
    elif documento.data_emissao is not None:
        referencia, qual = documento.data_emissao, "da emissão"
    else:
        return NOTA_NEUTRA, "documento sem data: a data não pesou"

    atraso = (dia - referencia).days
    nota = max(0.0, 1 - abs(atraso) / DIAS_PARA_ZERAR_A_DATA)
    if atraso == 0:
        return nota, f"{verbo} no dia {qual}"
    return nota, f"{verbo} {_dias(abs(atraso))} {'depois' if atraso > 0 else 'antes'} {qual}"


def _nota_nome(nome: str | None, memo: str) -> tuple[float, str]:
    if not nome:
        return NOTA_NEUTRA, "documento sem nome da contraparte: o nome não pesou"
    nucleo_do_memo = _nucleo(memo, sem_palavras_do_banco=True)
    if not nucleo_do_memo:
        return NOTA_NEUTRA, "memo sem nome para comparar"

    similaridade = fuzz.WRatio(_nucleo(nome), nucleo_do_memo) / 100
    nota = max(0.0, (similaridade - PISO_DE_SIMILARIDADE) / (1 - PISO_DE_SIMILARIDADE))
    if similaridade >= SIMILARIDADE_DE_NOME_PARECIDO:
        avaliacao = "nome parecido com o memo"
    elif similaridade >= PISO_DE_SIMILARIDADE:
        avaliacao = "nome pouco parecido com o memo"
    else:
        avaliacao = "nome diferente do memo"
    return nota, f"{avaliacao} ({similaridade:.0%})"


def _nucleo(texto: str, *, sem_palavras_do_banco: bool = False) -> str:
    """A parte do texto que identifica a contraparte: maiúsculas, sem acento, pontuação, números e sufixo de empresa."""
    sem_acento = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode("ascii").upper()
    palavras = [p for p in re.split(r"[^A-Z0-9]+", sem_acento) if p and not p.isdigit()]
    if sem_palavras_do_banco:
        palavras = [p for p in palavras if p not in _PALAVRAS_DO_BANCO]
    return _SUFIXO_DE_EMPRESA.sub("", " ".join(palavras))


def _cnpj_no_memo(cnpj: str | None, memo: str) -> bool:
    return bool(cnpj) and any(re.sub(r"\D", "", trecho) == cnpj for trecho in _NUMERO_COM_PONTUACAO.findall(memo))


def _motivo_valor(valor_documento: Decimal, valor_transacao: Decimal, diferenca: Decimal) -> str:
    if not diferenca:
        return f"valor igual ({_reais(valor_documento)})"
    return (
        f"valor bate com diferença de {_reais(diferenca)} "
        f"({_reais(valor_documento)} no documento, {_reais(abs(valor_transacao))} no banco)"
    )


def _dias(quantidade: int) -> str:
    return "1 dia" if quantidade == 1 else f"{quantidade} dias"


def _reais(valor: Decimal) -> str:
    return f"R$ {valor:,.2f}".translate(_PONTO_POR_VIRGULA)
