"""Contas e personas.

Uma **persona** é a identidade: voz, história, memória, arcos. Uma **conta**
é um login numa plataforma. Uma persona pode ter várias contas — o Instagram
e o WhatsApp da mesma personagem — e uma conta pode não ter persona nenhuma,
como as coletoras.

Sair de todos os devices não apaga nada: some o placement, a conta continua.
Ver ADR-0013.

Esta API é escrita para milhares de contas: paginação no banco, busca por
trigrama, facetas contadas em uma passada e ações em lote.
"""

from __future__ import annotations

import json
import re
from typing import Any, Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import text

from olimpo.core.db import session_scope
from olimpo.integrations.actions import ARCHETYPES_BY_PLATFORM, default_archetype

router = APIRouter(prefix="/v1", tags=["contas"])

#: Presença é o que a REALIDADE diz; `status` é o que NÓS queremos. Uma conta
#: pode estar `active` e há três dias fora do inventário — e é justamente essa
#: divergência que o operador precisa enxergar.
PRESENCE_SQL = """
    CASE
      WHEN NOT EXISTS (SELECT 1 FROM account_placements p WHERE p.account_id = a.id)
        THEN 'unbound'
      WHEN EXISTS (SELECT 1 FROM account_placements p
                    WHERE p.account_id = a.id AND p.logged_in
                      AND p.last_seen_at > now() - interval '1 hour')
        THEN 'online'
      WHEN a.last_seen_in_inventory > now() - interval '24 hours'
        THEN 'stale'
      ELSE 'missing'
    END
"""

SORTS = {
    "handle": "a.handle",
    "followers": "a.followers DESC NULLS LAST",
    "created": "a.created_at DESC",
    "seen": "a.last_seen_in_inventory DESC NULLS LAST",
    "status": "a.status, a.handle",
}


# =============================================================================
# Modelos
# =============================================================================

class AccountIn(BaseModel):
    handle: str = Field(min_length=1, max_length=120)
    platform: str
    archetype: str | None = None
    role: str = "agent"
    persona_id: UUID | None = None
    external_id: str | None = None
    profile: dict[str, Any] = {}
    labels: list[str] = []
    notes: str | None = None


class AccountPatch(BaseModel):
    handle: str | None = None
    archetype: str | None = None
    role: str | None = None
    status: str | None = None
    autonomy: str | None = None
    persona_id: UUID | None = None
    external_id: str | None = None
    jid: str | None = None
    timezone: str | None = None
    profile: dict[str, Any] | None = None
    secret_ref: str | None = None
    labels: list[str] | None = None
    metadata: dict[str, Any] | None = None
    notes: str | None = None
    cadence: dict[str, Any] | None = None
    #: Explícito para permitir desvincular — `None` em PATCH significa
    #: "não mexer", então precisa de um campo próprio.
    unbind_persona: bool = False


class BulkIn(BaseModel):
    ids: list[UUID] = Field(min_length=1, max_length=2000)
    action: Literal[
        "set_status", "set_autonomy", "bind_persona", "unbind_persona",
        "add_labels", "remove_labels", "set_role",
    ]
    value: Any = None


class PersonaIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    slug: str | None = None
    description: str | None = None
    persona_card: dict[str, Any] = {}
    persona_engine_id: str | None = None
    labels: list[str] = []
    notes: str | None = None


class PersonaPatch(BaseModel):
    name: str | None = None
    description: str | None = None
    persona_card: dict[str, Any] | None = None
    persona_engine_id: str | None = None
    labels: list[str] | None = None
    notes: str | None = None
    active: bool | None = None


class NoteIn(BaseModel):
    body: str = Field(min_length=1)
    author: str | None = None
    pinned: bool = False


def linha(row: Any) -> dict[str, Any]:
    """Converte uma linha do banco em algo serializável.

    `active_hours` é um `int4range`, que o driver devolve como objeto próprio
    e o serializador do FastAPI não conhece — a resposta quebrava DEPOIS de
    a escrita já ter acontecido, que é o pior momento possível.
    """
    out: dict[str, Any] = {}
    for k, v in dict(row).items():
        if type(v).__name__ == "Range":
            out[k] = {"inicio": v.lower, "fim": v.upper}
        else:
            out[k] = v
    return out


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.strip().lstrip("@").lower()).strip("-")


# =============================================================================
# Listagem — o caminho quente
# =============================================================================

@router.get("/accounts")
async def list_accounts(
    q: str | None = None,
    platform: str | None = None,
    status: str | None = None,
    role: str | None = None,
    archetype: str | None = None,
    autonomy: str | None = None,
    presence: str | None = None,
    label: str | None = None,
    persona_id: UUID | None = None,
    unbound: bool = False,
    sort: str = "handle",
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=500),
) -> dict[str, Any]:
    """Lista paginada com busca, filtros e contagem por faceta.

    Nada de trazer tudo e filtrar na tela: com milhares de contas isso
    derruba o navegador e some com o tempo de resposta.
    """
    where: list[str] = []
    params: dict[str, Any] = {}

    if q:
        # `handle` usa índice trigrama; os demais campos entram como apoio.
        where.append(
            "(a.handle ILIKE :q OR a.external_id = :qexato "
            " OR p.name ILIKE :q OR a.jid ILIKE :q)"
        )
        params["q"] = f"%{q.strip().lstrip('@')}%"
        params["qexato"] = q.strip()
    if platform:
        where.append("a.platform = CAST(:platform AS platform)")
        params["platform"] = platform
    if status:
        where.append("a.status = CAST(:status AS account_status)")
        params["status"] = status
    if archetype:
        where.append("a.archetype = CAST(:archetype AS archetype)")
        params["archetype"] = archetype
    if autonomy:
        where.append("a.autonomy = CAST(:autonomy AS autonomy_level)")
        params["autonomy"] = autonomy
    if role:
        where.append("a.role = :role")
        params["role"] = role
    if label:
        where.append(":label = ANY(a.labels)")
        params["label"] = label
    if persona_id:
        where.append("a.persona_id = :persona_id")
        params["persona_id"] = persona_id
    if unbound:
        where.append("a.persona_id IS NULL")
    if presence:
        where.append(f"({PRESENCE_SQL}) = :presence")
        params["presence"] = presence

    clause = f"WHERE {' AND '.join(where)}" if where else ""
    order = SORTS.get(sort, SORTS["handle"])
    params["limit"] = per_page
    params["offset"] = (page - 1) * per_page

    async with session_scope() as s:
        rows = (await s.execute(text(f"""
            SELECT a.id, a.handle, a.platform, a.archetype, a.role, a.status,
                   a.autonomy, a.followers, a.following, a.media_count,
                   a.external_id, a.jid, a.labels, a.last_seen_in_inventory,
                   a.last_action_at, a.created_at,
                   p.id AS persona_id, p.name AS persona_name,
                   ({PRESENCE_SQL}) AS presence,
                   (SELECT count(*) FROM account_placements pl
                     WHERE pl.account_id = a.id) AS placements,
                   (SELECT count(*) FROM entity_notes n
                     WHERE n.subject_type = 'account' AND n.subject_id = a.id) AS notes_count
              FROM accounts a
              LEFT JOIN personas p ON p.id = a.persona_id
              {clause}
             ORDER BY {order}
             LIMIT :limit OFFSET :offset
        """), params)).mappings().all()

        total = (await s.execute(text(f"""
            SELECT count(*) FROM accounts a
            LEFT JOIN personas p ON p.id = a.persona_id {clause}
        """), params)).scalar() or 0

        # Facetas sem filtro: são o mapa da operação inteira, e é isso que o
        # operador usa para decidir o que olhar primeiro.
        facetas = (await s.execute(text(f"""
            SELECT jsonb_build_object(
              'platform', (SELECT jsonb_object_agg(k, n) FROM
                 (SELECT platform::text k, count(*) n FROM accounts GROUP BY 1) x),
              'status',   (SELECT jsonb_object_agg(k, n) FROM
                 (SELECT status::text k, count(*) n FROM accounts GROUP BY 1) x),
              'role',     (SELECT jsonb_object_agg(k, n) FROM
                 (SELECT role k, count(*) n FROM accounts GROUP BY 1) x),
              'archetype',(SELECT jsonb_object_agg(k, n) FROM
                 (SELECT archetype::text k, count(*) n FROM accounts GROUP BY 1) x),
              'presence', (SELECT jsonb_object_agg(k, n) FROM
                 (SELECT ({PRESENCE_SQL}) k, count(*) n FROM accounts a GROUP BY 1) x),
              'unbound',  (SELECT count(*) FROM accounts WHERE persona_id IS NULL)
            )
        """))).scalar()

    return {
        "accounts": [linha(r) for r in rows],
        "total": total,
        "page": page,
        "per_page": per_page,
        "pages": max(1, -(-total // per_page)),
        "facets": facetas,
    }


# =============================================================================
# Conta: criar, ler, alterar
# =============================================================================

@router.post("/accounts", status_code=201)
async def create_account(body: AccountIn) -> dict[str, Any]:
    """Cria uma conta à mão, sem depender de o inventário revelá-la.

    É a ordem natural: cadastra-se o login, e o device aparece depois.
    """
    if body.platform not in ARCHETYPES_BY_PLATFORM:
        raise HTTPException(400, f"plataforma desconhecida: {body.platform}")
    archetype = body.archetype or default_archetype(body.platform)
    if archetype not in ARCHETYPES_BY_PLATFORM[body.platform]:
        raise HTTPException(
            400,
            f"arquétipo {archetype!r} não vale para {body.platform} — "
            f"use um de {sorted(ARCHETYPES_BY_PLATFORM[body.platform])}",
        )
    handle = body.handle.strip()
    if body.platform != "whatsapp" and not handle.startswith("@"):
        handle = f"@{handle}"

    async with session_scope() as s:
        if (await s.execute(text(
            "SELECT 1 FROM accounts WHERE platform = CAST(:p AS platform) AND handle = :h"
        ), {"p": body.platform, "h": handle})).first():
            raise HTTPException(409, f"{handle} já existe em {body.platform}")

        row = (await s.execute(text("""
            INSERT INTO accounts (handle, platform, archetype, role, status, autonomy,
                                  persona_id, external_id, profile, labels, notes)
            VALUES (:handle, CAST(:platform AS platform), CAST(:archetype AS archetype),
                    :role, 'registered', 'L0', :persona_id, :external_id,
                    CAST(:profile AS jsonb), :labels, :notes)
            RETURNING *
        """), {
            "handle": handle, "platform": body.platform, "archetype": archetype,
            "role": body.role, "persona_id": body.persona_id,
            "external_id": body.external_id, "profile": json.dumps(body.profile),
            "labels": body.labels, "notes": body.notes,
        })).mappings().one()
    return linha(row)


@router.get("/accounts/{account_id}")
async def get_account(account_id: UUID) -> dict[str, Any]:
    async with session_scope() as s:
        row = (await s.execute(text(f"""
            SELECT a.*, ({PRESENCE_SQL}) AS presence,
                   p.name AS persona_name, p.slug AS persona_slug
              FROM accounts a LEFT JOIN personas p ON p.id = a.persona_id
             WHERE a.id = :id
        """), {"id": account_id})).mappings().first()
        if not row:
            raise HTTPException(404, "conta não encontrada")

        placements = (await s.execute(text("""
            SELECT pl.*, sv.name AS server_name, d.alive, d.android_version,
                   d.adb_target, d.video_device, d.under_external_management
              FROM account_placements pl
              LEFT JOIN devices d  ON d.id = pl.device_id
              LEFT JOIN servers sv ON sv.id = d.server_id
             WHERE pl.account_id = :id
             ORDER BY pl.last_seen_at DESC
        """), {"id": account_id})).mappings().all()

        notes = (await s.execute(text("""
            SELECT * FROM entity_notes
             WHERE subject_type = 'account' AND subject_id = :id
             ORDER BY pinned DESC, created_at DESC LIMIT 100
        """), {"id": account_id})).mappings().all()

        tasks = (await s.execute(text("""
            SELECT id, type, status, scheduled_for, evidence_level, last_error
              FROM tasks WHERE account_id = :id
             ORDER BY created_at DESC LIMIT 20
        """), {"id": account_id})).mappings().all()

    return {
        "account": linha(row),
        "placements": [linha(p) for p in placements],
        "notes": [linha(n) for n in notes],
        "tasks": [linha(t) for t in tasks],
    }


@router.patch("/accounts/{account_id}")
async def update_account(account_id: UUID, body: AccountPatch) -> dict[str, Any]:
    fields = {k: v for k, v in body.model_dump().items()
              if v is not None and k != "unbind_persona"}
    if body.unbind_persona:
        fields["persona_id"] = None
    if not fields:
        raise HTTPException(400, "nada para atualizar")

    casts = {
        "status": "account_status", "autonomy": "autonomy_level",
        "archetype": "archetype",
    }
    jsons = {"profile", "metadata", "cadence"}
    sets = []
    params: dict[str, Any] = {"id": account_id}
    for k, v in fields.items():
        if k in casts:
            sets.append(f"{k} = CAST(:{k} AS {casts[k]})")
        elif k in jsons:
            sets.append(f"{k} = CAST(:{k} AS jsonb)")
            v = json.dumps(v)
        else:
            sets.append(f"{k} = :{k}")
        params[k] = v

    async with session_scope() as s:
        # Trocar o handle preserva o anterior: a conta não sumiu, ela se
        # renomeou — e concluir o contrário é como se perde o histórico.
        if "handle" in fields:
            await s.execute(text("""
                UPDATE accounts
                   SET previous_handles = array_append(previous_handles, handle)
                 WHERE id = :id AND handle <> :handle
            """), {"id": account_id, "handle": fields["handle"]})

        row = (await s.execute(
            text(f"UPDATE accounts SET {', '.join(sets)} WHERE id = :id RETURNING *"),
            params,
        )).mappings().first()
    if not row:
        raise HTTPException(404, "conta não encontrada")
    return linha(row)


@router.delete("/accounts/{account_id}", status_code=204)
async def delete_account(account_id: UUID, confirm: str = "") -> None:
    """Apagar cascateia memória, posts, tasks e aprovações.

    Conta perdida deve virar `status = 'lost'`, não desaparecer: o histórico
    é o que permite entender por que ela caiu.
    """
    if confirm != "apagar-de-verdade":
        raise HTTPException(
            400,
            "Apagar remove todo o histórico em cascata. Para conta banida use "
            "status='lost'. Se for mesmo apagar, envie confirm=apagar-de-verdade.",
        )
    async with session_scope() as s:
        await s.execute(text("DELETE FROM accounts WHERE id = :id"), {"id": account_id})


@router.post("/accounts/bulk")
async def bulk(body: BulkIn) -> dict[str, Any]:
    """Ação em lote. Sem isto, operar milhares de contas é impraticável."""
    acoes = {
        "set_status": ("status = CAST(:v AS account_status)", str),
        "set_autonomy": ("autonomy = CAST(:v AS autonomy_level)", str),
        "set_role": ("role = :v", str),
        "bind_persona": ("persona_id = CAST(:v AS uuid)", str),
        "unbind_persona": ("persona_id = NULL", None),
        "add_labels": ("labels = (SELECT array_agg(DISTINCT x) FROM "
                       "unnest(labels || CAST(:v AS text[])) x)", list),
        "remove_labels": ("labels = (SELECT COALESCE(array_agg(x), '{}') FROM "
                          "unnest(labels) x WHERE NOT (x = ANY(CAST(:v AS text[]))))", list),
    }
    sql, tipo = acoes[body.action]
    if tipo is not None and body.value is None:
        raise HTTPException(400, f"a ação {body.action} exige um valor")

    params: dict[str, Any] = {"ids": body.ids}
    if tipo is not None:
        params["v"] = body.value

    async with session_scope() as s:
        res = await s.execute(
            text(f"UPDATE accounts SET {sql} WHERE id = ANY(:ids) RETURNING id"), params
        )
        afetadas = len(res.fetchall())
    return {"updated": afetadas, "action": body.action}


# =============================================================================
# Observações
# =============================================================================

@router.post("/accounts/{account_id}/notes", status_code=201)
async def add_note(account_id: UUID, body: NoteIn) -> dict[str, Any]:
    async with session_scope() as s:
        row = (await s.execute(text("""
            INSERT INTO entity_notes (subject_type, subject_id, body, author, pinned)
            VALUES ('account', :id, :body, :author, :pinned) RETURNING *
        """), {"id": account_id, **body.model_dump()})).mappings().one()
    return linha(row)


@router.delete("/notes/{note_id}", status_code=204)
async def delete_note(note_id: UUID) -> None:
    async with session_scope() as s:
        await s.execute(text("DELETE FROM entity_notes WHERE id = :id"), {"id": note_id})


# =============================================================================
# Personas
# =============================================================================

@router.get("/personas")
async def list_personas(q: str | None = None) -> dict[str, Any]:
    where = "WHERE p.name ILIKE :q" if q else ""
    async with session_scope() as s:
        rows = (await s.execute(text(f"""
            SELECT p.*,
                   COALESCE((
                     SELECT jsonb_agg(jsonb_build_object(
                              'id', a.id, 'handle', a.handle, 'platform', a.platform,
                              'status', a.status, 'followers', a.followers))
                       FROM accounts a WHERE a.persona_id = p.id), '[]'::jsonb) AS accounts,
                   (SELECT count(*) FROM memories m WHERE m.persona_id = p.id) AS memories,
                   (SELECT title FROM story_arcs
                     WHERE persona_id = p.id AND active LIMIT 1) AS arc
              FROM personas p {where}
             ORDER BY p.name
        """), {"q": f"%{q}%"} if q else {})).mappings().all()
    return {"personas": [linha(r) for r in rows]}


@router.post("/personas", status_code=201)
async def create_persona(body: PersonaIn) -> dict[str, Any]:
    slug = body.slug or _slug(body.name)
    async with session_scope() as s:
        if (await s.execute(
            text("SELECT 1 FROM personas WHERE slug = :s"), {"s": slug}
        )).first():
            raise HTTPException(409, f"já existe uma persona com o identificador {slug!r}")
        row = (await s.execute(text("""
            INSERT INTO personas (name, slug, description, persona_card,
                                  persona_engine_id, labels, notes)
            VALUES (:name, :slug, :description, CAST(:persona_card AS jsonb),
                    :persona_engine_id, :labels, :notes)
            RETURNING *
        """), {
            **body.model_dump(exclude={"slug", "persona_card"}),
            "slug": slug, "persona_card": json.dumps(body.persona_card),
        })).mappings().one()
    return linha(row)


@router.get("/personas/{persona_id}")
async def get_persona(persona_id: UUID) -> dict[str, Any]:
    async with session_scope() as s:
        row = (await s.execute(
            text("SELECT * FROM personas WHERE id = :id"), {"id": persona_id}
        )).mappings().first()
        if not row:
            raise HTTPException(404, "persona não encontrada")
        accounts = (await s.execute(text(f"""
            SELECT a.id, a.handle, a.platform, a.archetype, a.status, a.autonomy,
                   a.followers, ({PRESENCE_SQL}) AS presence
              FROM accounts a WHERE a.persona_id = :id ORDER BY a.platform
        """), {"id": persona_id})).mappings().all()
        arcs = (await s.execute(text(
            "SELECT * FROM story_arcs WHERE persona_id = :id ORDER BY starts_at DESC LIMIT 10"
        ), {"id": persona_id})).mappings().all()
        notes = (await s.execute(text("""
            SELECT * FROM entity_notes WHERE subject_type = 'persona' AND subject_id = :id
             ORDER BY pinned DESC, created_at DESC LIMIT 50
        """), {"id": persona_id})).mappings().all()
    return {
        "persona": linha(row),
        "accounts": [linha(a) for a in accounts],
        "arcs": [linha(a) for a in arcs],
        "notes": [linha(n) for n in notes],
    }


@router.patch("/personas/{persona_id}")
async def update_persona(persona_id: UUID, body: PersonaPatch) -> dict[str, Any]:
    fields = {k: v for k, v in body.model_dump().items() if v is not None}
    if not fields:
        raise HTTPException(400, "nada para atualizar")
    sets, params = [], {"id": persona_id}
    for k, v in fields.items():
        if k == "persona_card":
            sets.append("persona_card = CAST(:persona_card AS jsonb)")
            sets.append("persona_version = persona_version + 1")
            v = json.dumps(v)
        else:
            sets.append(f"{k} = :{k}")
        params[k] = v

    async with session_scope() as s:
        row = (await s.execute(
            text(f"UPDATE personas SET {', '.join(sets)} WHERE id = :id RETURNING *"),
            params,
        )).mappings().first()
        # Toda mudança de card vira versão: "por que ela mudou de voz?"
        # precisa continuar respondível.
        if row and "persona_card" in fields:
            await s.execute(text("""
                INSERT INTO persona_versions (account_id, version, persona_card,
                                              changed_by, change_note)
                SELECT a.id, :v, CAST(:card AS jsonb), 'painel', 'edição de persona'
                  FROM accounts a WHERE a.persona_id = :id
                ON CONFLICT (account_id, version) DO NOTHING
            """), {"id": persona_id, "v": row["persona_version"],
                   "card": json.dumps(fields["persona_card"])})
    if not row:
        raise HTTPException(404, "persona não encontrada")
    return linha(row)


@router.post("/personas/{persona_id}/notes", status_code=201)
async def add_persona_note(persona_id: UUID, body: NoteIn) -> dict[str, Any]:
    async with session_scope() as s:
        row = (await s.execute(text("""
            INSERT INTO entity_notes (subject_type, subject_id, body, author, pinned)
            VALUES ('persona', :id, :body, :author, :pinned) RETURNING *
        """), {"id": persona_id, **body.model_dump()})).mappings().one()
    return linha(row)
