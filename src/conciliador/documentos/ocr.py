"""OCR: pré-processamento com OpenCV e leitura com Tesseract em português.

O Tesseract é bom com página limpa, reta e de fundo branco; foto de recibo e scan torto não são isso. Cada etapa
de `preprocessar` desfaz um defeito específico, e o comentário dela diz qual.
"""

from __future__ import annotations

import io
from collections.abc import Sequence
from dataclasses import dataclass

import cv2
import numpy as np
import pytesseract
from PIL import Image, ImageOps, UnidentifiedImageError

IDIOMA = "por"
CONFIG_TESSERACT = "--oem 1 --psm 3"  # motor LSTM; segmentação automática (documento tem blocos e colunas)
LARGURA_MINIMA = 1800  # px: abaixo disso a letra de corpo fica pequena demais para o Tesseract
INCLINACAO_MAXIMA = 5.0  # graus: mais que isso é foto de lado, não scan torto


class ImagemInvalida(ValueError):
    """O conteúdo não é uma imagem legível."""


@dataclass(frozen=True)
class ResultadoOCR:
    texto: str
    confianca_media: float  # 0 a 100: média da confiança do Tesseract nas palavras lidas
    palavras: int

    @classmethod
    def juntar(cls, resultados: Sequence[ResultadoOCR]) -> ResultadoOCR:
        """Junta páginas; a confiança é a média ponderada pelo número de palavras."""
        palavras = sum(r.palavras for r in resultados)
        soma = sum(r.confianca_media * r.palavras for r in resultados)
        return cls("\n".join(r.texto for r in resultados), round(soma / palavras, 2) if palavras else 0.0, palavras)


def abrir_imagem(conteudo: bytes) -> Image.Image:
    try:
        imagem = Image.open(io.BytesIO(conteudo))
        imagem.load()
    except (UnidentifiedImageError, OSError) as erro:
        raise ImagemInvalida(f"imagem ilegível: {erro}") from erro
    # Foto de celular costuma vir "deitada" com a orientação certa só anotada no EXIF.
    return ImageOps.exif_transpose(imagem)


def girar(imagem: np.ndarray, angulo: float, borda: int = 255) -> np.ndarray:
    """Gira em `angulo` graus no sentido anti-horário, mantendo o tamanho."""
    altura, largura = imagem.shape[:2]
    matriz = cv2.getRotationMatrix2D((largura / 2, altura / 2), angulo, 1.0)
    return cv2.warpAffine(imagem, matriz, (largura, altura), flags=cv2.INTER_CUBIC,
                          borderMode=cv2.BORDER_CONSTANT, borderValue=borda)


def estimar_inclinacao(cinza: np.ndarray) -> float:
    """Inclinação do texto em graus, anti-horário positivo (mesma convenção de `PIL.Image.rotate`).

    Gira uma cópia reduzida da página em vários ângulos e fica com o que deixa o perfil horizontal de tinta mais
    "pontudo": com as linhas retas, a tinta se concentra em faixas e os vãos entre linhas ficam vazios.
    """
    escala = min(1.0, 1000 / max(cinza.shape))
    pequena = cv2.resize(cinza, None, fx=escala, fy=escala, interpolation=cv2.INTER_AREA)
    _, tinta = cv2.threshold(pequena, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)

    def nitidez(angulo: float) -> float:
        perfil = girar(tinta, angulo, borda=0).sum(axis=1, dtype=np.float64)
        return float(np.sum(np.diff(perfil) ** 2))

    grosso = max(np.arange(-INCLINACAO_MAXIMA, INCLINACAO_MAXIMA + 0.01, 0.5), key=nitidez)
    fino = max(np.arange(grosso - 0.5, grosso + 0.51, 0.1), key=nitidez)
    return round(-float(fino), 2)  # o ângulo que endireita é o oposto da inclinação


def _uniformizar_fundo(cinza: np.ndarray) -> np.ndarray:
    """Divide a imagem pelo fundo estimado: sombra, papel amarelado e mesa em volta viram branco."""
    # O fundo sai de um fechamento morfológico (apaga tudo que é menor que o núcleo, ou seja, a tinta), feito em
    # 1/4 do tamanho porque é só uma estimativa suave e assim custa quase nada.
    pequena = cv2.resize(cinza, None, fx=0.25, fy=0.25, interpolation=cv2.INTER_AREA)
    fundo = cv2.morphologyEx(pequena, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_RECT, (9, 9)))
    fundo = cv2.resize(cv2.GaussianBlur(fundo, (5, 5), 0), (cinza.shape[1], cinza.shape[0]),
                       interpolation=cv2.INTER_LINEAR)
    return cv2.divide(cinza, np.maximum(fundo, 1), scale=255)


def _mascara_da_folha(cinza: np.ndarray) -> np.ndarray | None:
    """Em foto, a folha é a maior região clara. Devolve a máscara dela, ou None se ela ocupa o quadro todo (scan)."""
    pequena = cv2.resize(cinza, None, fx=0.25, fy=0.25, interpolation=cv2.INTER_AREA)
    _, clara = cv2.threshold(cv2.GaussianBlur(pequena, (5, 5), 0), 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    contornos, _ = cv2.findContours(clara, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contornos:
        return None
    folha = cv2.convexHull(max(contornos, key=cv2.contourArea))
    if not 0.3 <= cv2.contourArea(folha) / clara.size <= 0.97:
        return None
    mascara = np.zeros_like(pequena)
    cv2.fillPoly(mascara, [folha], 255)
    # Recua um pouco da borda: a passagem mesa -> papel vira um tracejado que o Tesseract lê como letra.
    mascara = cv2.erode(mascara, np.ones((5, 5), np.uint8))
    return cv2.resize(mascara, (cinza.shape[1], cinza.shape[0]), interpolation=cv2.INTER_NEAREST)


def preprocessar(imagem: Image.Image) -> np.ndarray:
    # Escala de cinza: o Tesseract trabalha com luminância; cor só traz papel amarelado e carimbo azul.
    cinza = np.asarray(imagem.convert("L"))
    # Escala: com letra de menos de ~20 px de altura o Tesseract erra bem mais; foto pequena é ampliada.
    if cinza.shape[1] < LARGURA_MINIMA:
        fator = LARGURA_MINIMA / cinza.shape[1]
        cinza = cv2.resize(cinza, None, fx=fator, fy=fator, interpolation=cv2.INTER_CUBIC)
    # Ruído: a mediana 3x3 apaga os pontinhos de scan e de sensor sem borrar a borda da letra (o gaussiano borra).
    cinza = cv2.medianBlur(cinza, 3)
    # Recorte: em foto, a mesa em volta da folha não é documento; tudo fora da folha vira papel branco.
    folha = _mascara_da_folha(cinza)
    # Iluminação: sombra de foto quebra qualquer limiar único; com o fundo uniforme, um limiar só resolve.
    cinza = _uniformizar_fundo(cinza)
    if folha is not None:
        cinza[folha == 0] = 255
    # Inclinação: com linhas tortas a segmentação em linhas do Tesseract corta e mistura palavras.
    inclinacao = estimar_inclinacao(cinza)
    if abs(inclinacao) >= 0.1:
        cinza = girar(cinza, -inclinacao)
    # Binarização Otsu: escolhe o limiar entre papel e tinta pelo histograma, sem número mágico por documento.
    _, binaria = cv2.threshold(cinza, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    return _apagar_linhas(binaria)


def _apagar_linhas(binaria: np.ndarray) -> np.ndarray:
    """Apaga moldura, linha de tabela e traço de assinatura.

    O Tesseract toma caixa por figura e às vezes pula o texto de dentro (o valor do recibo, num quadro, sumia).
    Linha é tinta contínua bem mais comprida que qualquer letra: uma abertura com núcleo de 1 px de espessura e
    1/25 da página de comprimento só deixa passar isso.
    """
    altura, largura = binaria.shape
    tinta = cv2.bitwise_not(binaria)
    horizontais = cv2.morphologyEx(tinta, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (largura // 25, 1)))
    verticais = cv2.morphologyEx(tinta, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (1, altura // 25)))
    linhas = cv2.dilate(cv2.bitwise_or(horizontais, verticais), np.ones((3, 3), np.uint8))
    return cv2.bitwise_or(binaria, linhas)


def ler_imagem(imagem: Image.Image) -> ResultadoOCR:
    dados = pytesseract.image_to_data(preprocessar(imagem), lang=IDIOMA, config=CONFIG_TESSERACT,
                                      output_type=pytesseract.Output.DICT)
    return montar_resultado(dados)


def montar_resultado(dados: dict[str, list]) -> ResultadoOCR:
    """Remonta o texto linha a linha a partir do `image_to_data` (uma chamada só dá texto e confiança)."""
    linhas: dict[tuple[int, int, int, int], list[str]] = {}
    confiancas: list[float] = []
    for i, palavra in enumerate(dados["text"]):
        palavra = (palavra or "").strip()
        if int(dados["level"][i]) != 5 or not palavra:
            continue
        chave = (int(dados["page_num"][i]), int(dados["block_num"][i]), int(dados["par_num"][i]),
                 int(dados["line_num"][i]))
        linhas.setdefault(chave, []).append(palavra)
        if (confianca := float(dados["conf"][i])) >= 0:
            confiancas.append(confianca)

    partes: list[str] = []
    bloco_anterior: tuple[int, int] | None = None
    for (pagina, bloco, _, _), palavras in linhas.items():
        if bloco_anterior is not None and (pagina, bloco) != bloco_anterior:
            partes.append("")  # linha em branco entre blocos, como no image_to_string
        partes.append(" ".join(palavras))
        bloco_anterior = (pagina, bloco)
    media = round(sum(confiancas) / len(confiancas), 2) if confiancas else 0.0
    return ResultadoOCR("\n".join(partes), media, len(confiancas))
