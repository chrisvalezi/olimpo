#!/usr/bin/env python3
"""Baixa para disco toda a mídia que hoje só existe em CDN de terceiros.

Motivo: 69 das 127 URLs apontam para o CDN do Higgsfield, que vai ser
desligado, e 57 para o do fal, que expira. São as imagens de identidade das
personas — o ativo que garante que uma personagem não muda de rosto entre
posts. Perder isso não tem conserto: não dá para regenerar o mesmo rosto.

Nomeia por hash do conteúdo, como a origem já fazia, para deduplicar e para
o mapeamento URL → arquivo ser reproduzível.

    python3 scripts/preservar_midia.py
"""

from __future__ import annotations

import hashlib
import json
import pathlib
import sys
import urllib.error
import urllib.request

RAIZ = pathlib.Path(__file__).resolve().parent.parent / "import"
DESTINO = RAIZ / "remote"
MAPA = DESTINO / "mapa.json"

#: CDNs mandam `application/octet-stream` com frequência, então o formato é
#: detectado pelos bytes iniciais — não pelo cabeçalho nem pela extensão.
ASSINATURAS: list[tuple[bytes, str]] = [
    (b"\x89PNG\r\n\x1a\n", "png"),
    (b"\xff\xd8\xff", "jpg"),
    (b"GIF8", "gif"),
    (b"RIFF", "webp"),
    (b"\x00\x00\x00\x18ftyp", "mp4"),
    (b"\x00\x00\x00\x1cftyp", "mp4"),
    (b"\x00\x00\x00 ftyp", "mp4"),
]


def extensao(dados: bytes) -> str:
    for assinatura, ext in ASSINATURAS:
        if dados.startswith(assinatura):
            return ext
    if b"ftyp" in dados[:32]:
        return "mp4"
    return "bin"


def baixar(url: str, tentativas: int = 3) -> bytes | None:
    for tentativa in range(tentativas):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "olimpo/preserva"})
            with urllib.request.urlopen(req, timeout=90) as resp:
                return resp.read()
        except urllib.error.HTTPError as exc:
            # 403/404 não melhoram com nova tentativa.
            if exc.code in (403, 404, 410):
                return None
        except Exception:
            if tentativa == tentativas - 1:
                return None
    return None


def main() -> int:
    urls = [u.strip() for u in (DESTINO / "urls.txt").read_text().splitlines() if u.strip()]
    DESTINO.mkdir(parents=True, exist_ok=True)
    mapa: dict[str, str] = json.loads(MAPA.read_text()) if MAPA.exists() else {}

    ok = falhou = pulou = 0
    perdidas: list[str] = []

    for i, url in enumerate(urls, 1):
        if url in mapa and (DESTINO / mapa[url]).exists():
            pulou += 1
            continue

        dados = baixar(url)
        if not dados:
            falhou += 1
            perdidas.append(url)
            print(f"  [{i}/{len(urls)}] ✗ {url[:80]}")
            continue

        nome = f"{hashlib.sha256(dados).hexdigest()[:24]}.{extensao(dados)}"
        (DESTINO / nome).write_bytes(dados)
        mapa[url] = nome
        ok += 1
        if i % 10 == 0 or i == len(urls):
            print(f"  [{i}/{len(urls)}] {ok} baixadas, {falhou} perdidas")
        MAPA.write_text(json.dumps(mapa, indent=2))

    print(f"\n✓ {ok} baixadas · {pulou} já existiam · {falhou} perdidas")
    if perdidas:
        (DESTINO / "perdidas.txt").write_text("\n".join(perdidas))
        print(f"  URLs que não respondem em {DESTINO / 'perdidas.txt'}")
        print("  (essas já estavam perdidas — o CDN não as tem mais)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
