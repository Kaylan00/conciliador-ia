"""Os arquivos de dados/exemplos/ estão em dia com cenario.json e com scripts/gerar_extrato.py."""

import importlib.util
import json
import sys
from decimal import Decimal
from pathlib import Path

from conciliador.extrato import ler_extrato

RAIZ = Path(__file__).resolve().parents[1]
EXEMPLOS = RAIZ / "dados" / "exemplos"


def _carregar_gerador(monkeypatch):
    spec = importlib.util.spec_from_file_location("gerar_extrato", RAIZ / "scripts" / "gerar_extrato.py")
    modulo = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, modulo)  # dataclass procura o próprio módulo em sys.modules
    spec.loader.exec_module(modulo)
    return modulo


def test_arquivos_de_exemplo_sao_exatamente_o_que_o_gerador_produz(monkeypatch):
    gerador = _carregar_gerador(monkeypatch)
    cenario = json.loads((EXEMPLOS / "cenario.json").read_text(encoding="utf-8"))
    movimentos = gerador.movimentos(cenario)

    assert (EXEMPLOS / "extrato_outubro.ofx").read_bytes() == gerador.gerar_ofx(movimentos, cenario["empresa"])
    assert (EXEMPLOS / "extrato_outubro.csv").read_bytes() == gerador.gerar_csv(movimentos, cenario["empresa"])
    assert (EXEMPLOS / "gabarito_conciliacao.json").read_bytes() == gerador.gerar_gabarito(movimentos, cenario)


def test_saldo_final_do_ofx_e_o_saldo_inicial_mais_a_movimentacao():
    cenario = json.loads((EXEMPLOS / "cenario.json").read_text(encoding="utf-8"))
    conteudo = (EXEMPLOS / "extrato_outubro.ofx").read_bytes()

    movimentacao = sum(t.valor for t in ler_extrato(conteudo, "extrato_outubro.ofx"))

    saldo_final = Decimal(conteudo.decode("cp1252").split("<BALAMT>")[1].split()[0])
    assert saldo_final == Decimal(cenario["empresa"]["saldo_inicial"]) + movimentacao
