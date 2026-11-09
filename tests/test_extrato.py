import pytest

from conciliador.extrato import ExtratoInvalido, ler_extrato


@pytest.mark.parametrize(
    ("conteudo", "mensagem"),
    [
        (b"", "o arquivo está vazio"),
        (b"  \n\t", "o arquivo está vazio"),
        (b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n", "não é um extrato, é um PDF"),
        (b"PK\x03\x04\x14\x00\x06\x00", "planilha do Excel (.xlsx)"),
        (b"\x89PNG\r\n\x1a\n\x00\x00", "imagem PNG"),
        (b"GIF89a\x01\x00\x01\x00\x00\x00", "arquivo binário"),
    ],
)
def test_arquivo_que_nao_e_extrato_da_erro_claro(conteudo, mensagem):
    with pytest.raises(ExtratoInvalido, match="^extrato_outubro: ") as erro:
        ler_extrato(conteudo, "extrato_outubro")

    assert mensagem in str(erro.value)


def test_extensao_ofx_com_conteudo_que_nao_e_ofx():
    with pytest.raises(ExtratoInvalido, match="a extensão é OFX, mas o conteúdo não tem o cabeçalho OFX"):
        ler_extrato(b"Data;Historico;Valor\n12/10/2026;X;-1,00\n", "banco.OFX")


def test_cabecalho_ofx_sem_o_bloco_ofx():
    with pytest.raises(ExtratoInvalido, match="falta o bloco <OFX>"):
        ler_extrato(b"OFXHEADER:100\nDATA:OFXSGML\nVERSION:102\n\n", "banco.ofx")


def test_o_formato_vem_do_conteudo_e_nao_da_extensao():
    csv_com_nome_de_txt = b"Data;Historico;Valor\n12/10/2026;TARIFA;-24,90\n"

    [transacao] = ler_extrato(csv_com_nome_de_txt, "extrato.txt")

    assert transacao.descricao == "TARIFA"
