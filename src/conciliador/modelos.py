"""Contrato de dados compartilhado por todos os módulos.

Dinheiro é sempre Decimal com 2 casas. CNPJ é só dígitos (14). Datas são date, sem hora.
Quem mudar este arquivo muda o contrato de todo mundo: combine antes (ver AGENTS.md).
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, Field


class TipoDocumento(StrEnum):
    NFE = "nfe"  # nota fiscal de produto (XML ou DANFE)
    NFSE = "nfse"  # nota fiscal de serviço
    FATURA = "fatura"  # conta de consumo: energia, internet, aluguel de sistema
    RECIBO = "recibo"  # recibo simples, muitas vezes escaneado ou fotografado
    BOLETO = "boleto"
    OUTRO = "outro"


class OrigemTexto(StrEnum):
    XML = "xml"  # dado estruturado, sem leitura de texto
    PDF_TEXTO = "pdf_texto"  # PDF com camada de texto
    OCR = "ocr"  # imagem ou PDF escaneado, lido com Tesseract


class Direcao(StrEnum):
    """Ponto de vista da empresa dona dos livros."""

    PAGAR = "pagar"  # a empresa comprou: fornecedor emitiu contra ela
    RECEBER = "receber"  # a empresa vendeu: ela emitiu contra o cliente


class Problema(BaseModel):
    """Motivo auditável que derruba a confiança de uma extração ou de um par conciliado."""

    codigo: str  # ex.: "cnpj_invalido", "total_diferente_dos_itens", "valor_nao_encontrado_no_texto"
    mensagem: str  # frase que a pessoa da revisão lê
    penalidade: float = Field(ge=0, le=1)


class ItemDocumento(BaseModel):
    descricao: str
    quantidade: Decimal = Decimal("1")
    valor_unitario: Decimal
    valor_total: Decimal


class DocumentoExtraido(BaseModel):
    tipo: TipoDocumento
    direcao: Direcao = Direcao.PAGAR
    emitente_nome: str | None = None
    emitente_cnpj: str | None = None
    destinatario_nome: str | None = None
    destinatario_cnpj: str | None = None
    numero: str | None = None
    data_emissao: date | None = None
    data_vencimento: date | None = None
    valor_total: Decimal | None = None
    itens: list[ItemDocumento] = []
    descricao: str | None = None  # resumo curto do que foi comprado ou vendido
    origem: OrigemTexto
    texto: str = ""  # texto bruto que serviu de base (vazio para XML)
    confianca: float = Field(default=1.0, ge=0, le=1)
    problemas: list[Problema] = []

    @property
    def contraparte_nome(self) -> str | None:
        return self.emitente_nome if self.direcao == Direcao.PAGAR else self.destinatario_nome

    @property
    def contraparte_cnpj(self) -> str | None:
        return self.emitente_cnpj if self.direcao == Direcao.PAGAR else self.destinatario_cnpj


class TransacaoBancaria(BaseModel):
    id: str  # FITID do OFX ou hash estável da linha do CSV
    data: date
    valor: Decimal  # negativo = saiu da conta, positivo = entrou
    descricao: str  # memo do banco, como veio
    tipo: str | None = None  # TRNTYPE do OFX: DEBIT, CREDIT, FEE, INT, XFER...


class ParConciliado(BaseModel):
    transacao_id: str
    documento_id: str
    score: float = Field(ge=0, le=1)
    motivos: list[str]  # por que casou: "valor igual", "2 dias depois do vencimento", "nome do fornecedor no memo"


class ResultadoConciliacao(BaseModel):
    pares: list[ParConciliado]
    transacoes_sem_documento: list[str]  # tarifa, rendimento, pagamento sem nota...
    documentos_sem_transacao: list[str]  # ainda não pago / não recebido


class Partida(BaseModel):
    conta: str  # código do plano de contas, ex.: "4.2.1"
    debito: Decimal = Decimal("0")
    credito: Decimal = Decimal("0")


class StatusLancamento(StrEnum):
    SUGERIDO = "sugerido"
    APROVADO = "aprovado"
    REJEITADO = "rejeitado"


class Lancamento(BaseModel):
    data: date
    historico: str
    partidas: list[Partida]
    documento_id: str | None = None
    transacao_id: str | None = None
    status: StatusLancamento = StatusLancamento.SUGERIDO
    justificativa: str = ""  # por que essa conta: regra que bateu ou resposta do modelo

    @property
    def fechado(self) -> bool:
        """Partidas dobradas: soma dos débitos igual à soma dos créditos."""
        return sum(p.debito for p in self.partidas) == sum(p.credito for p in self.partidas)
