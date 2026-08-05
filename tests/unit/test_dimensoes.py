"""Leitura de dimensões a partir do cabeçalho.

Escrito à mão em vez de usar Pillow: são dois inteiros nos primeiros bytes,
e a dependência traria libjpeg, zlib e libwebp para dentro da imagem.

O risco de escrever à mão é reconhecer o formato e devolver número errado —
pior que devolver None, porque ninguém verifica. Daí os casos abaixo terem
valores conferidos, não só "não é None".
"""

from __future__ import annotations

import struct

import pytest

from olimpo.media.armazenamento import dimensoes


def _png(largura: int, altura: int) -> bytes:
    return (
        b"\x89PNG\r\n\x1a\n"
        + struct.pack(">I", 13) + b"IHDR"
        + struct.pack(">II", largura, altura)
        + b"\x08\x06\x00\x00\x00"
    )


def _jpeg(largura: int, altura: int, *, com_lixo: bool = False) -> bytes:
    corpo = b"\xff\xd8"
    if com_lixo:
        # APP0 (JFIF) e um COM antes do SOF — o caso comum de verdade. Um
        # parser que assumisse offset fixo leria o tamanho do comentário.
        corpo += b"\xff\xe0" + struct.pack(">H", 16) + b"JFIF\x00" + b"\x00" * 9
        corpo += b"\xff\xfe" + struct.pack(">H", 10) + b"comentario"[:8]
    corpo += b"\xff\xc0" + struct.pack(">H", 17) + b"\x08"
    corpo += struct.pack(">HH", altura, largura)  # altura ANTES da largura
    corpo += b"\x03" + b"\x00" * 9
    return corpo


def _gif(largura: int, altura: int) -> bytes:
    return b"GIF89a" + struct.pack("<HH", largura, altura) + b"\x00" * 7


def _webp_vp8l(largura: int, altura: int) -> bytes:
    # 14 bits para cada dimensão, guardadas como "valor - 1".
    bits = (largura - 1) | ((altura - 1) << 14)
    return (
        b"RIFF" + b"\x00" * 4 + b"WEBP" + b"VP8L"
        + b"\x00" * 4 + b"\x2f" + struct.pack("<I", bits)
    )


@pytest.mark.parametrize(
    ("nome", "conteudo", "esperado"),
    [
        ("png retrato", _png(1152, 2048), (1152, 2048)),
        ("png paisagem", _png(2048, 1152), (2048, 1152)),
        ("jpeg simples", _jpeg(1536, 1024), (1536, 1024)),
        # O que mais quebra parser ingênuo: segmentos antes do SOF.
        ("jpeg com jfif e comentario", _jpeg(1024, 1536, com_lixo=True), (1024, 1536)),
        ("gif", _gif(480, 270), (480, 270)),
        ("webp vp8l", _webp_vp8l(800, 600), (800, 600)),
    ],
)
def test_le_dimensoes(nome: str, conteudo: bytes, esperado: tuple[int, int]) -> None:
    assert dimensoes(conteudo) == esperado, nome


def test_jpeg_nao_troca_largura_com_altura() -> None:
    """No JPEG a altura vem ANTES da largura no SOF.

    Trocar as duas passa despercebido em imagem quadrada e produz
    "1024x1536" para uma paisagem — que é justamente a informação que a tela
    usa para o operador conferir o formato que pediu.
    """
    assert dimensoes(_jpeg(1920, 1080)) == (1920, 1080)


@pytest.mark.parametrize("lixo", [
    b"", b"\x00", b"nao sou imagem", b"\xff\xd8", b"\x89PNG\r\n\x1a\n",
    b"RIFF" + b"\x00" * 4 + b"WEBP" + b"XXXX",
])
def test_nao_reconhecido_devolve_none(lixo: bytes) -> None:
    """Truncado ou desconhecido devolve None, não estoura.

    Guardar precisa continuar funcionando — a dimensão é um extra, e perder
    o arquivo por causa dela seria trocar um confortável por um grave.
    """
    assert dimensoes(lixo) is None
