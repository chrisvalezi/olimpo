#!/usr/bin/env python3
"""Verifica links internos quebrados na documentação.

Documentação que aponta para arquivo inexistente é pior que documentação
ausente: ela promete uma resposta que não existe.

    python scripts/check_docs.py

Sai com código 1 se houver link quebrado — pronto para virar passo de CI.
"""

import pathlib
import re
import sys

SKIP_DIRS = {"node_modules", ".next", ".git", ".venv", "__pycache__"}
LINK_RE = re.compile(r"\[[^\]]*\]\(([^)#\s]+)")


def main() -> int:
    root = pathlib.Path(__file__).resolve().parent.parent
    broken: list[str] = []
    checked = 0

    for md in sorted(root.rglob("*.md")):
        if any(part in SKIP_DIRS for part in md.parts):
            continue
        for link in LINK_RE.findall(md.read_text()):
            if link.startswith(("http://", "https://", "mailto:")):
                continue
            checked += 1
            if not (md.parent / link).resolve().exists():
                broken.append(f"{md.relative_to(root)}  →  {link}")

    print(f"{checked} link(s) interno(s) verificado(s).")
    if broken:
        print(f"\n✗ {len(broken)} quebrado(s):")
        for item in broken:
            print(f"   {item}")
        return 1

    print("✓ Nenhum link quebrado.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
