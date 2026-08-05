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
from pydantic import BaseModel, Field
from sqlalchemy import text

from olimpo.core.db import session_scope
from olimpo.media import modelos
from olimpo.media.servico import PedidoDeFoto
from olimpo.prompts import photo_studio
from olimpo.workers.tasks_geracao import enfileirar_foto

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


# =============================================================================
# Geração
# =============================================================================

class PedidoDeGeracao(BaseModel):
    """O que a tela envia. Tudo o mais vem da persona."""

    local: str = "coffee-shop"
    hora: str = "afternoon"
    pose: str = "front"
    stance: str = "standing"
    expressao: str = "natural"
    olhar: str = "at-camera"
    aspect_ratio: str = "9:16"
    quantidade: int = Field(default=1, ge=1, le=8)
    wardrobe_item_id: UUID | None = None
    wardrobe_text: str = ""
    hairstyle_text: str = ""
    brand_id: UUID | None = None
    prop_text: str = ""
    modelo: str | None = None
    resolucao: str | None = None
    #: Semente fixa reproduz o mesmo prompt. Serve para comparar variações
    #: mudando um parâmetro por vez — algo que a origem não permitia.
    seed: int | None = None


@router.get("/opcoes")
async def opcoes() -> dict[str, Any]:
    """Os valores que a tela oferece, vindos do próprio motor de prompt.

    Não são listas escritas à mão na interface: sair de sincronia com o
    motor produziria um pedido que gera prompt vazio no meio.
    """
    return {
        "locais": list(photo_studio.LOCATION_LABEL.keys()),
        "horas": list(photo_studio.TIME_ATMO.keys()),
        "poses": sorted(photo_studio.POSE_MAP.keys()),
        "expressoes": list(photo_studio.EXPRESSION_MAP.keys()),
        "modelos": [
            {"id": m.id, "label": m.label, "resolucoes": list(m.resolucoes)}
            for m in modelos.IMAGEM.values()
        ],
        "aspect_ratios": ["9:16", "4:5", "1:1", "16:9", "3:4"],
    }


@router.post("/personas/{persona_id}/gerar", status_code=202)
async def gerar(persona_id: UUID, body: PedidoDeGeracao) -> dict[str, Any]:
    """Enfileira uma geração. Devolve o job para acompanhar.

    Não espera pelo resultado: uma imagem leva de vinte segundos a alguns
    minutos, e segurar a conexão é como se perde trabalho já pago quando o
    processo reinicia.
    """
    async with session_scope() as s:
        persona = (await s.execute(text("""
            SELECT name, face_asset_id FROM personas WHERE id = :id
        """), {"id": persona_id})).mappings().first()
    if not persona:
        raise HTTPException(404, "persona não encontrada")
    if not persona["face_asset_id"]:
        raise HTTPException(
            400,
            f"{persona['name']} não tem referência de rosto. Sem ela cada "
            "geração produz uma pessoa diferente — gere o rosto antes.",
        )

    job_id = await enfileirar_foto(PedidoDeFoto(
        persona_id=persona_id, **body.model_dump()
    ))
    return {"job_id": str(job_id), "status": "queued"}


@router.get("/jobs/{job_id}")
async def estado_do_job(job_id: UUID) -> dict[str, Any]:
    async with session_scope() as s:
        job = (await s.execute(text("""
            SELECT g.*, p.name AS persona_name
              FROM generation_jobs g
              LEFT JOIN personas p ON p.id = g.persona_id
             WHERE g.id = :id
        """), {"id": job_id})).mappings().first()
    if not job:
        raise HTTPException(404, "job não encontrado")
    return dict(job)


@router.get("/jobs")
async def listar_jobs(
    persona_id: UUID | None = None,
    status: str | None = None,
    limite: int = Query(50, ge=1, le=200),
) -> dict[str, Any]:
    where, params = [], {"limite": limite}
    if persona_id:
        where.append("g.persona_id = :persona_id")
        params["persona_id"] = persona_id
    if status:
        where.append("g.status = CAST(:status AS generation_status)")
        params["status"] = status
    clause = f"WHERE {' AND '.join(where)}" if where else ""

    async with session_scope() as s:
        rows = (await s.execute(text(f"""
            SELECT g.id, g.persona_id, g.kind, g.status, g.progress, g.model,
                   g.route, g.error, g.created_at, g.finished_at,
                   g.result_asset_ids, p.name AS persona_name
              FROM generation_jobs g
              LEFT JOIN personas p ON p.id = g.persona_id
              {clause}
             ORDER BY g.created_at DESC LIMIT :limite
        """), params)).mappings().all()
    return {"jobs": [dict(r) for r in rows]}
