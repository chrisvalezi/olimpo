#!/usr/bin/env python3
"""Verifica links internos e âncoras de seção na documentação.

Documentação que aponta para arquivo ou seção inexistente é pior que
documentação ausente: ela promete uma resposta que não existe, e quem segue
o link perde tempo antes de descobrir.

    python scripts/check_docs.py

Sai com código 1 se houver link quebrado — pronto para virar passo de CI.
"""

import pathlib
import re
import sys
import unicodedata

SKIP_DIRS = {"node_modules", ".next", ".git", ".venv", "__pycache__", "import"}
LINK_RE = re.compile(r"\[[^\]]*\]\(([^)\s]+)\)")
TITULO_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$", re.M)


def slug(titulo: str) -> str:
    """Reproduz a regra do GitHub para âncoras de título.

    Detalhes que importam e que uma implementação ingênua erra:
    acentos são PRESERVADOS (não se normaliza para ASCII), marcação inline
    é removida mas o texto dentro dela permanece, e emoji some junto com a
    pontuação.
    """
    t = titulo.strip()
    t = re.sub(r"`([^`]*)`", r"\1", t)          # código inline
    t = re.sub(r"\*\*([^*]*)\*\*", r"\1", t)    # negrito
    t = re.sub(r"\*([^*]*)\*", r"\1", t)        # itálico
    t = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", t)  # link
    t = t.lower()
    # Remove tudo que não é letra, número, espaço ou hífen — mantendo
    # acentuados, que o GitHub preserva.
    t = "".join(
        c for c in t
        if c.isalnum() or c in " -_" or unicodedata.category(c).startswith("M")
    )
    # Cada espaço vira UM hífen — não se colapsa. É por isso que um título
    # com travessão ("A — B") produz âncora com dois hífens: o travessão some
    # e sobram os dois espaços que o cercavam.
    return t.strip().replace(" ", "-")


def main() -> int:
    raiz = pathlib.Path(__file__).resolve().parent.parent
    arquivos = [
        md for md in raiz.rglob("*.md")
        if not any(p in SKIP_DIRS for p in md.parts)
    ]

    ancoras: dict[pathlib.Path, set[str]] = {}
    for md in arquivos:
        vistos: dict[str, int] = {}
        conjunto: set[str] = set()
        for _, titulo in TITULO_RE.findall(md.read_text()):
            base = slug(titulo)
            # Títulos repetidos ganham sufixo -1, -2… como no GitHub.
            n = vistos.get(base, 0)
            conjunto.add(base if n == 0 else f"{base}-{n}")
            vistos[base] = n + 1
        ancoras[md.resolve()] = conjunto

    quebrados: list[str] = []
    verificados = 0

    for md in arquivos:
        for link in LINK_RE.findall(md.read_text()):
            if link.startswith(("http://", "https://", "mailto:")):
                continue
            arquivo, _, ancora = link.partition("#")
            verificados += 1

            alvo = (md.parent / arquivo).resolve() if arquivo else md.resolve()
            rel = md.relative_to(raiz)

            if arquivo and not alvo.exists():
                quebrados.append(f"{rel}  →  {link}   (arquivo)")
                continue
            if ancora and alvo in ancoras and ancora not in ancoras[alvo]:
                quebrados.append(f"{rel}  →  {link}   (seção)")

    print(f"{verificados} link(s) interno(s) verificado(s).")
    if quebrados:
        print(f"\n✗ {len(quebrados)} quebrado(s):")
        for item in quebrados:
            print(f"   {item}")
        return 1

    print("✓ Nenhum link quebrado.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
