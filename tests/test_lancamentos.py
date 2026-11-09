import json
from datetime import date
from decimal import Decimal

import httpx
import pytest

from conciliador import cenario
from conciliador import plano_contas as pc
from conciliador.classificador_llm import ClassificadorLLM
from conciliador.lancamentos import (
    balancete,
    classificar_documento,
    classificar_transacao,
    sugerir_lancamentos,
)
from conciliador.modelos import (
    Direcao,
    DocumentoExtraido,
    OrigemTexto,
    ParConciliado,
    ResultadoConciliacao,
    TipoDocumento,
    TransacaoBancaria,
)


@pytest.fixture(scope="module")
def cen():
    return cenario.carregar()


def conciliacao_do_gabarito(cen) -> ResultadoConciliacao:
    pares = [
        ParConciliado(transacao_id=t, documento_id=d, score=1.0, motivos=["gabarito"])
        for t, d in cen.par_esperado.items()
        if d is not None
    ]
    pagos = {p.documento_id for p in pares}
    return ResultadoConciliacao(
        pares=pares,
        transacoes_sem_documento=[t for t, d in cen.par_esperado.items() if d is None],
        documentos_sem_transacao=[d for d in cen.documentos if d not in pagos],
    )


def test_regras_classificam_todos_os_documentos_do_cenario(cen):
    erros = {
        doc_id: (classificar_documento(doc).conta, cen.conta_esperada[doc_id])
        for doc_id, doc in cen.documentos.items()
        if classificar_documento(doc).conta != cen.conta_esperada[doc_id]
    }
    assert erros == {}


def test_tarifa_e_rendimento_sem_documento(cen):
    por_memo = {t.descricao: t for t in cen.transacoes}
    assert classificar_transacao(por_memo["TARIFA PACOTE SERVICOS"]).conta == "4.3.1"
    assert classificar_transacao(por_memo["RENDIMENTO CONTA REMUNERADA"]).conta == "3.2.1"


def test_pix_sem_nota_fica_para_alguem_decidir(cen):
    pix = next(t for t in cen.transacoes if t.descricao == "PIX ENVIADO JOAO PEDRO S")
    cls = classificar_transacao(pix)
    assert cls.conta == pc.A_CLASSIFICAR
    assert cls.confianca == 0.0


def test_todos_os_lancamentos_fecham_e_o_balancete_bate(cen):
    sugestoes = sugerir_lancamentos(cen.documentos, cen.transacoes, conciliacao_do_gabarito(cen))
    assert sugestoes.lancamentos
    assert all(lanc.fechado for lanc in sugestoes.lancamentos)

    linhas = {linha.conta: linha for linha in balancete(sugestoes.lancamentos)}
    assert sum(linha.debitos for linha in linhas.values()) == sum(linha.creditos for linha in linhas.values())
    # o banco no balancete é exatamente o movimento do extrato
    assert linhas[pc.BANCO].saldo == sum(t.valor for t in cen.transacoes)
    # tudo que foi comprado e pago zerou fornecedores e contas a pagar
    assert linhas[pc.FORNECEDORES].saldo == 0
    assert linhas[pc.CONTAS_A_PAGAR].saldo == 0
    # a venda para o hotel ainda não foi recebida: continua em clientes
    assert linhas[pc.CLIENTES].saldo == Decimal("1150.00")


def test_venda_gera_receita_e_baixa_em_clientes(cen):
    sugestoes = sugerir_lancamentos(cen.documentos, cen.transacoes, conciliacao_do_gabarito(cen))
    da_venda = [lanc for lanc in sugestoes.lancamentos if lanc.documento_id == "doc-09"]
    registro, recebimento = sorted(da_venda, key=lambda lanc: lanc.data)
    assert {(p.conta, p.debito, p.credito) for p in registro.partidas} == {
        (pc.CLIENTES, Decimal("5200.00"), Decimal("0")),
        ("3.1.1", Decimal("0"), Decimal("5200.00")),
    }
    assert recebimento.data == date(2026, 10, 20)
    assert {p.conta for p in recebimento.partidas} == {pc.BANCO, pc.CLIENTES}


def _doc_sem_pista() -> DocumentoExtraido:
    return DocumentoExtraido(
        tipo=TipoDocumento.RECIBO,
        direcao=Direcao.PAGAR,
        origem=OrigemTexto.OCR,
        emitente_nome="Fulano de Tal",
        descricao="Serviço prestado conforme combinado",
        valor_total=Decimal("300.00"),
        data_emissao=date(2026, 10, 3),
    )


def _cliente_falso(resposta: dict | None = None, status: int = 200, conteudo: str | None = None):
    def responder(request: httpx.Request) -> httpx.Response:
        if conteudo is None and resposta is None:
            raise httpx.ConnectError("sem rede", request=request)
        texto = conteudo if conteudo is not None else json.dumps(resposta)
        return httpx.Response(
            status,
            json={"choices": [{"message": {"content": texto}}], "usage": {"total_tokens": 120}},
        )

    return httpx.Client(base_url="https://llm.teste", transport=httpx.MockTransport(responder))


def test_modelo_so_entra_quando_as_regras_nao_resolvem():
    classificador = ClassificadorLLM(_cliente_falso({"conta": "4.2.3", "justificativa": "serviço de terceiro"}))
    cls = classificar_documento(_doc_sem_pista(), modelo=classificador)
    assert (cls.conta, cls.fonte) == ("4.2.3", "modelo")
    assert classificador.chamadas[0]["uso"]["total_tokens"] == 120


def test_resposta_do_modelo_fora_da_lista_e_descartada():
    classificador = ClassificadorLLM(_cliente_falso({"conta": "9.9.9", "justificativa": "inventei"}))
    cls = classificar_documento(_doc_sem_pista(), modelo=classificador)
    assert cls.conta == pc.A_CLASSIFICAR
    assert cls.fonte == "sem_classificacao"


@pytest.mark.parametrize("conteudo", ["isso não é json", '{"conta": '])
def test_json_quebrado_do_modelo_vira_none(conteudo):
    assert ClassificadorLLM(_cliente_falso(conteudo=conteudo))("x", pc.plano_padrao().folhas) is None


def test_falha_de_rede_nao_derruba_a_classificacao():
    cls = classificar_documento(_doc_sem_pista(), modelo=ClassificadorLLM(_cliente_falso()))
    assert cls.conta == pc.A_CLASSIFICAR


def test_regras_tem_prioridade_e_nao_chamam_o_modelo(cen):
    classificador = ClassificadorLLM(_cliente_falso({"conta": "4.2.6", "justificativa": "x"}))
    classificar_documento(cen.documentos["doc-01"], modelo=classificador)
    assert classificador.chamadas == []


def test_transacao_positiva_sem_pista_nao_vira_despesa():
    t = TransacaoBancaria(id="x", data=date(2026, 10, 2), valor=Decimal("80"), descricao="PIX RECEBIDO MARIA")
    assert classificar_transacao(t).conta.startswith("3")
