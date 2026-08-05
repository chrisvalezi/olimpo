"""Detecção de HEIC/HEIF.

O ponto frágil não é converter — é **decidir** converter. O mime declarado
pelo cliente não serve: o Safari manda `image/heic`, o Chrome no Windows
manda `application/octet-stream` para o mesmo arquivo, e `curl` não manda
nada. Errar essa decisão guarda um HEIC cru, e a falha só aparece meses
depois, na geração, sem relação óbvia com o upload.

Os casos abaixo montam cabeçalhos ISO-BMFF à mão. Isso é de propósito: o
teste precisa cobrir marcas que não existem em nenhum arquivo à mão, e
precisa rodar sem depender do libheif estar instalado.
"""

from __future__ import annotations

import struct

import pytest

from olimpo.media.conversao import eh_heif


def _ftyp(marca: bytes, compativeis: bytes = b"") -> bytes:
    """Cabeçalho ISO-BMFF: [tamanho][ftyp][marca][versão][compatíveis…]."""
    corpo = b"ftyp" + marca + struct.pack(">I", 0) + compativeis
    return struct.pack(">I", len(corpo) + 4) + corpo + b"\x00" * 32


@pytest.mark.parametrize("marca", [b"heic", b"heix", b"heim", b"mif1", b"msf1"])
def test_reconhece_marcas_de_heif(marca: bytes) -> None:
    assert eh_heif(_ftyp(marca)) is True


def test_reconhece_marca_entre_as_compativeis() -> None:
    """HEIC de câmera que não é iPhone às vezes traz a marca principal
    genérica e a real na lista de compatíveis."""
    assert eh_heif(_ftyp(b"mp42", b"mp41heic")) is True


def test_mime_declarado_basta_quando_confiavel() -> None:
    """Safari manda o mime certo; nesse caso nem precisa olhar os bytes."""
    assert eh_heif(b"", "image/heic") is True
    assert eh_heif(b"", "image/heif") is True


def test_mime_mentiroso_nao_engana() -> None:
    """O caso real: HEIC declarado como JPEG.

    Acontece quando o cliente adivinha pelo nome do arquivo, e quem
    renomeou `.heic` para `.jpg` acha que resolveu.
    """
    assert eh_heif(_ftyp(b"heic"), "image/jpeg") is True


@pytest.mark.parametrize("conteudo", [
    b"",
    b"\xff\xd8\xff\xe0" + b"\x00" * 40,                      # JPEG
    b"\x89PNG\r\n\x1a\n" + b"\x00" * 40,                     # PNG
    b"RIFF" + b"\x00" * 4 + b"WEBP" + b"VP8 " + b"\x00" * 30,  # WebP
    b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 40,              # MP4 de verdade
    b"ftyp",                                                  # truncado
])
def test_nao_confunde_outros_formatos(conteudo: bytes) -> None:
    """Falso positivo aqui é pior que falso negativo: mandaria um JPEG
    perfeitamente bom para o decodificador de HEIF e o upload falharia."""
    assert eh_heif(conteudo) is False


def test_marca_desalinhada_nao_conta() -> None:
    """As marcas compatíveis são alinhadas de 4 em 4.

    Quatro bytes que casem fora do alinhamento são coincidência dentro de
    dados — não uma declaração de formato. Aceitar isso mandaria arquivo
    legítimo para o decodificador errado.
    """
    # "xxheic" começa no byte 2 da região de compatíveis: desalinhado.
    assert eh_heif(_ftyp(b"mp42", b"xxheicxx")) is False


def test_marca_alem_do_tamanho_da_caixa_nao_conta() -> None:
    """O tamanho declarado no início delimita onde as marcas acabam.

    Depois disso são outras caixas — e num vídeo, dados arbitrários.
    """
    cabecalho = _ftyp(b"mp42")            # ftyp sem compatíveis
    assert eh_heif(cabecalho + b"heic" * 8) is False


def test_mp4_nao_e_heif() -> None:
    """Vídeo entra pelo mesmo endpoint de upload e usa o MESMO container
    ISO-BMFF. Confundir os dois converteria um vídeo em uma imagem — ou
    falharia o upload de vídeo, que é o caminho legítimo."""
    mp4 = b"\x00\x00\x00\x20ftypisom\x00\x00\x02\x00isomiso2avc1mp41"
    assert eh_heif(mp4, "video/mp4") is False


# =============================================================================
# Detecção do tipo real
# =============================================================================

def test_detecta_tipos_pelos_bytes() -> None:
    """`curl` manda `application/octet-stream` para `.webp` e `.mp4`.

    Antes disso o upload era recusado pelo envelope e não pelo conteúdo — e
    a mensagem culpava o arquivo.
    """
    from olimpo.media.conversao import detectar_mime

    casos = [
        (b"\xff\xd8\xff\xe0" + b"\x00" * 20, "image/jpeg"),
        (b"\x89PNG\r\n\x1a\n" + b"\x00" * 20, "image/png"),
        (b"GIF89a" + b"\x00" * 20, "image/gif"),
        (b"RIFF" + b"\x00" * 4 + b"WEBP" + b"VP8 " + b"\x00" * 20, "image/webp"),
        (b"\x1a\x45\xdf\xa3" + b"\x00" * 20, "video/webm"),
        (b"\x00\x00\x00\x20ftypisom" + b"\x00" * 20, "video/mp4"),
        (b"\x00\x00\x00\x14ftypqt  " + b"\x00" * 20, "video/quicktime"),
        (_ftyp(b"heic"), "image/heic"),
    ]
    for conteudo, esperado in casos:
        assert detectar_mime(conteudo) == esperado, esperado


def test_nao_inventa_tipo_para_lixo() -> None:
    """Devolver um palpite errado seria pior que devolver None: o arquivo
    entraria no acervo com o tipo errado e falharia na geração."""
    from olimpo.media.conversao import detectar_mime

    for lixo in (b"", b"nao sou nada", b"\x00" * 40, b"%PDF-1.4"):
        assert detectar_mime(lixo) is None
