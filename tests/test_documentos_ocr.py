import io

import numpy as np
import pytest
from PIL import Image, ImageDraw, ImageFilter, ImageFont

from conciliador.documentos.ocr import (
    ImagemInvalida,
    ResultadoOCR,
    abrir_imagem,
    estimar_inclinacao,
    ler_imagem,
    montar_resultado,
    preprocessar,
)

LINHAS = [
    "RECIBO",
    "Recebemos de Ateliê Ponto Firme Ltda a importância de",
    "R$ 1.234,56 referente a conserto de máquina de costura.",
    "Vitória, 22/10/2026.",
]


def fonte(tamanho: int) -> ImageFont.ImageFont:
    try:
        return ImageFont.truetype("DejaVuSans.ttf", tamanho)
    except OSError:
        return ImageFont.load_default(tamanho)


def recibo(angulo: float = 0.0, ruido: float = 0.0) -> Image.Image:
    imagem = Image.new("L", (1700, 1000), 255)
    tela = ImageDraw.Draw(imagem)
    for indice, linha in enumerate(LINHAS):
        tela.text((100, 120 + indice * 90), linha, fill=0, font=fonte(40))
    imagem = imagem.rotate(angulo, resample=Image.Resampling.BICUBIC, fillcolor=255)
    if ruido:
        pixels = np.asarray(imagem, dtype=np.float32) + np.random.default_rng(7).normal(0, ruido, (1000, 1700))
        imagem = Image.fromarray(np.clip(pixels, 0, 255).astype(np.uint8)).filter(ImageFilter.GaussianBlur(0.8))
    return imagem


@pytest.mark.parametrize("angulo", [0.0, 2.5, -1.5, 4.0])
def test_estima_a_inclinacao_do_texto(angulo):
    cinza = np.asarray(recibo(angulo))
    assert estimar_inclinacao(cinza) == pytest.approx(angulo, abs=0.3)


def test_preprocessamento_devolve_imagem_binaria_e_reta():
    binaria = preprocessar(recibo(angulo=3.0, ruido=12))
    assert binaria.dtype == np.uint8
    assert set(np.unique(binaria)) <= {0, 255}
    assert binaria.shape[1] >= 1800  # imagem estreita é ampliada
    assert estimar_inclinacao(binaria) == pytest.approx(0.0, abs=0.3)


def test_le_recibo_torto_e_ruidoso():
    resultado = ler_imagem(recibo(angulo=2.0, ruido=12))
    assert "R$ 1.234,56" in resultado.texto
    assert "22/10/2026" in resultado.texto
    assert resultado.confianca_media > 70
    assert resultado.palavras >= 20


def test_monta_texto_e_confianca_a_partir_do_image_to_data():
    dados = {
        "level": [1, 5, 5, 5, 5, 5],
        "page_num": [1, 1, 1, 1, 1, 1],
        "block_num": [0, 1, 1, 1, 2, 2],
        "par_num": [0, 1, 1, 1, 1, 1],
        "line_num": [0, 1, 1, 2, 1, 1],
        "text": ["", "Valor", "total", "R$", "  ", "10,00"],
        "conf": [-1, 90, 80, 70, -1, 60],
    }
    resultado = montar_resultado(dados)
    assert resultado.texto == "Valor total\nR$\n\n10,00"  # linha em branco entre blocos
    assert resultado.confianca_media == 75.0  # média das palavras; -1 (sem confiança) fica de fora
    assert resultado.palavras == 4


def test_juntar_paginas_pondera_pelo_numero_de_palavras():
    juntas = ResultadoOCR.juntar([ResultadoOCR("a", 90.0, 30), ResultadoOCR("b", 60.0, 10)])
    assert (juntas.texto, juntas.confianca_media, juntas.palavras) == ("a\nb", 82.5, 40)
    assert ResultadoOCR.juntar([]).confianca_media == 0.0


def test_abrir_imagem_respeita_a_orientacao_do_exif():
    foto = Image.new("RGB", (300, 100), "white")
    exif = Image.Exif()
    exif[0x0112] = 6  # câmera segurada em pé: girar 90° para ler
    saida = io.BytesIO()
    foto.save(saida, "JPEG", exif=exif)
    assert abrir_imagem(saida.getvalue()).size == (100, 300)


def test_abrir_imagem_invalida():
    with pytest.raises(ImagemInvalida):
        abrir_imagem(b"isto nao e imagem")
