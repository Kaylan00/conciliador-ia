"""API de ponta a ponta com leitores falsos: testa rotas, persistência e regras de aprovação sem OCR."""

import pytest
from fastapi.testclient import TestClient

from conciliador import cenario
from conciliador import plano_contas as pc
from conciliador.api import Dependencias, criar_app
from conciliador.banco import Banco
from conciliador.modelos import ParConciliado, ResultadoConciliacao


@pytest.fixture()
def cliente(tmp_path):
    cen = cenario.carregar()

    def ler_documento(conteudo, nome, cnpj):
        return cen.documentos[conteudo.decode()]

    def ler_extrato(conteudo, nome):
        if conteudo != b"extrato":
            raise ValueError("isso não é um extrato OFX nem CSV")
        return cen.transacoes

    def conciliar(docs, transacoes):
        id_salvo = {cen_id: salvo_id for salvo_id, d in docs.items() for cen_id, c in cen.documentos.items() if d == c}
        pares = [
            ParConciliado(transacao_id=t, documento_id=id_salvo[d], score=0.95, motivos=["valor igual"])
            for t, d in cen.par_esperado.items()
            if d is not None and d in id_salvo
        ]
        usados = {p.documento_id for p in pares}
        return ResultadoConciliacao(
            pares=pares,
            transacoes_sem_documento=[t for t, d in cen.par_esperado.items() if d is None],
            documentos_sem_transacao=[i for i in docs if i not in usados],
        )

    dep = Dependencias(
        banco=Banco(f"sqlite:///{tmp_path / 'teste.db'}"),
        ler_documento=ler_documento,
        ler_extrato=ler_extrato,
        conciliar=conciliar,
    )
    c = TestClient(criar_app(dep))
    arquivos = [("arquivos", (f"{doc_id}.pdf", doc_id.encode(), "application/pdf")) for doc_id in cen.documentos]
    assert c.post("/api/documentos", files=arquivos).status_code == 200
    assert c.post("/api/extratos", files={"arquivo": ("out.ofx", b"extrato")}).json()["novas"] == len(cen.transacoes)
    return c


def test_processar_concilia_e_sugere_lancamentos(cliente):
    resumo = cliente.post("/api/processar").json()
    assert resumo["pares"] == 11
    assert resumo["documentos_sem_transacao"] == 1  # a venda ainda não recebida
    assert resumo["transacoes_sem_documento"] == 3
    lancs = cliente.get("/api/lancamentos").json()
    assert len(lancs) == 12 + 11 + 3  # registros + baixas + avulsos
    assert all(lanc["status"] == "sugerido" for lanc in lancs)


def test_reimportar_extrato_nao_duplica(cliente):
    assert cliente.post("/api/extratos", files={"arquivo": ("out.ofx", b"extrato")}).json()["novas"] == 0


def test_extrato_invalido_responde_422(cliente):
    resposta = cliente.post("/api/extratos", files={"arquivo": ("foto.jpg", b"nao sou extrato")})
    assert resposta.status_code == 422
    assert "extrato" in resposta.json()["detail"]


def test_aprovado_sobrevive_ao_reprocessamento(cliente):
    cliente.post("/api/processar")
    alvo = cliente.get("/api/lancamentos").json()[0]
    assert cliente.post(f"/api/lancamentos/{alvo['id']}/aprovar").status_code == 200
    cliente.post("/api/processar")
    lancs = cliente.get("/api/lancamentos").json()
    aprovados = [lanc for lanc in lancs if lanc["status"] == "aprovado"]
    assert [a["historico"] for a in aprovados] == [alvo["historico"]]
    assert len(lancs) == 26  # o aprovado não voltou como sugestão duplicada


def test_nao_aprova_lancamento_sem_conta(cliente):
    cliente.post("/api/processar")
    pix = next(lanc for lanc in cliente.get("/api/lancamentos").json() if "JOAO PEDRO" in lanc["historico"])
    assert cliente.post(f"/api/lancamentos/{pix['id']}/aprovar").status_code == 422

    assert cliente.put(f"/api/lancamentos/{pix['id']}/conta", json={"conta": "4.2.3"}).status_code == 200
    assert cliente.post(f"/api/lancamentos/{pix['id']}/aprovar").status_code == 200


@pytest.mark.parametrize("conta", ["1.1.1", "4.2", "9.9.9"])
def test_troca_para_conta_de_controle_ou_grupo_e_recusada(cliente, conta):
    cliente.post("/api/processar")
    alvo = next(lanc for lanc in cliente.get("/api/lancamentos").json() if lanc["historico"].startswith("Registro"))
    assert cliente.put(f"/api/lancamentos/{alvo['id']}/conta", json={"conta": conta}).status_code == 422


def test_balancete_fecha(cliente):
    cliente.post("/api/processar")
    linhas = cliente.get("/api/balancete").json()
    debitos = sum(float(linha["debitos"]) for linha in linhas)
    assert debitos == pytest.approx(sum(float(linha["creditos"]) for linha in linhas))
    banco = next(linha for linha in linhas if linha["conta"] == pc.BANCO)
    assert float(banco["saldo"]) == pytest.approx(-6252.35)  # movimento do mês: saídas maiores que entradas


def test_tela_de_revisao_mostra_pendencias_e_lancamentos(cliente):
    cliente.post("/api/processar")
    html = cliente.get("/").text
    assert "Precisa de você" in html
    assert "ainda não recebido" in html  # venda para o hotel
    assert "PIX ENVIADO JOAO PEDRO S" in html
    assert "Balancete" in html


def test_arquivo_original_para_conferir(cliente):
    doc_id = next(iter(cliente.get("/api/documentos").json()))
    resposta = cliente.get(f"/api/documentos/{doc_id}/arquivo")
    assert resposta.status_code == 200
    assert resposta.headers["content-type"] == "application/pdf"
