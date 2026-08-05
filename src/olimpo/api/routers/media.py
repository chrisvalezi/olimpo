"""Mídia: identidade visual das personas e conteúdo gerado.

Os arquivos ficam no armazenamento do Olimpo, não no CDN do provider. Não é
preciosismo: as URLs do fal expiram e o CDN de onde essas imagens vieram vai
ser desligado. Uma imagem de identidade perdida não se regenera — não existe
como pedir "o mesmo rosto de antes".
"""

from __future__ import annotations

import pathlib
from typing import Any
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse
from sqlalchemy import text

from olimpo.core.db import session_scope

router = APIRouter(prefix="/v1/media", tags=["mídia"])

RAIZ = pathlib.Path("/app/media")

#: Ordem em que as âncoras de identidade entram no array de referências.
#: É contrato semântico do prompt: a primeira é o rosto, a segunda é a roupa.
#: Reordenar faz o modelo copiar a roupa da referência facial.
ORDEM_IDENTIDADE = [
    ("face_asset_id", "face", "rosto"),
    ("character_sheet_asset_id", "character_sheet", "folha de personagem"),
    ("close_up_face_asset_id", "close_up_face", "close de rosto"),
    ("close_up_detail_asset_id", "close_up_detail", "cartela de detalhe"),
]


@router.get("/{asset_id}/arquivo")
async def servir(asset_id: UUID) -> FileResponse:
    """Serve o arquivo. Cache longo: o nome vem do hash do conteúdo, então
    um arquivo nunca muda — se mudasse, teria outro id."""
    async with session_scope() as s:
        caminho = (await s.execute(
            text("SELECT storage_path FROM media_assets WHERE id = :id"),
            {"id": asset_id},
        )).scalar()
    if not caminho:
        raise HTTPException(404, "mídia não encontrada")

    arquivo = RAIZ / caminho
    # O caminho vem do banco, mas confiar nele sem checar permitiria que uma
    # linha adulterada lesse qualquer arquivo do container.
    try:
        arquivo.resolve().relative_to(RAIZ.resolve())
    except ValueError:
        raise HTTPException(400, "caminho inválido") from None
    if not arquivo.exists():
        raise HTTPException(404, "arquivo ausente no armazenamento")

    return FileResponse(
        arquivo, headers={"Cache-Control": "public, max-age=31536000, immutable"}
    )


@router.get("")
async def listar(
    persona_id: UUID | None = None,
    role: str | None = None,
    kind: str | None = None,
    page: int = Query(1, ge=1),
    per_page: int = Query(60, ge=1, le=200),
) -> dict[str, Any]:
    where, params = [], {"limit": per_page, "offset": (page - 1) * per_page}
    if persona_id:
        where.append("m.persona_id = :persona_id")
        params["persona_id"] = persona_id
    if role:
        where.append("m.role = CAST(:role AS media_role)")
        params["role"] = role
    if kind:
        where.append("m.kind = CAST(:kind AS media_kind)")
        params["kind"] = kind
    clause = f"WHERE {' AND '.join(where)}" if where else ""

    async with session_scope() as s:
        rows = (await s.execute(text(f"""
            SELECT m.id, m.kind, m.role, m.label, m.source, m.provider, m.model,
                   m.aspect_ratio, m.width, m.height, m.bytes, m.created_at,
                   m.prompt, p.name AS persona_name
              FROM media_assets m
              LEFT JOIN personas p ON p.id = m.persona_id
              {clause}
             ORDER BY m.created_at DESC
             LIMIT :limit OFFSET :offset
        """), params)).mappings().all()
        total = (await s.execute(
            text(f"SELECT count(*) FROM media_assets m {clause}"), params
        )).scalar() or 0

    return {
        "media": [dict(r) for r in rows],
        "total": total, "page": page,
        "pages": max(1, -(-total // per_page)),
    }


@router.get("/personas/{persona_id}/identidade")
async def identidade(persona_id: UUID) -> dict[str, Any]:
    """As âncoras de identidade da persona, na ordem que a geração usa.

    Quantas existem importa mais que quais: com as quatro, o rosto se mantém
    entre gerações. Faltando a primeira, não há de onde ancorar.
    """
    async with session_scope() as s:
        p = (await s.execute(text("""
            SELECT id, name, face_asset_id, character_sheet_asset_id,
                   close_up_face_asset_id, close_up_detail_asset_id,
                   reference_asset_ids
              FROM personas WHERE id = :id
        """), {"id": persona_id})).mappings().first()
        if not p:
            raise HTTPException(404, "persona não encontrada")

        ids = [p[coluna] for coluna, _, _ in ORDEM_IDENTIDADE if p[coluna]]
        detalhes = {}
        if ids:
            for r in (await s.execute(text("""
                SELECT id, role, storage_path, width, height, bytes
                  FROM media_assets WHERE id = ANY(:ids)
            """), {"ids": ids})).mappings().all():
                detalhes[str(r["id"])] = dict(r)

    ancoras = []
    for posicao, (coluna, papel, rotulo) in enumerate(ORDEM_IDENTIDADE, start=1):
        asset_id = p[coluna]
        ancoras.append({
            # A posição é o que o prompt referencia como "@imageN".
            "posicao": posicao,
            "papel": papel,
            "rotulo": rotulo,
            "asset_id": str(asset_id) if asset_id else None,
            "detalhe": detalhes.get(str(asset_id)) if asset_id else None,
        })

    completas = sum(1 for a in ancoras if a["asset_id"])
    return {
        "persona": {"id": str(p["id"]), "name": p["name"]},
        "ancoras": ancoras,
        "completas": completas,
        "extras": [str(i) for i in (p["reference_asset_ids"] or [])],
        # Sem a primeira âncora não há identidade a preservar: qualquer
        # geração produz uma pessoa diferente.
        "pode_gerar_com_identidade": bool(p["face_asset_id"]),
    }
