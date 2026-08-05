"""Registro de nós do android-orchestrator.

Cada host com containers redroid roda a própria pilha do orchestrator e só
conhece a si mesmo — não existe índice de frota do lado deles. A lista de
nós é do Olimpo, e a visão de frota se monta por fan-out.

Ver docs/contratos/android-orchestrator.md §9 e ADR-0011.
"""

from __future__ import annotations

import asyncio
from typing import Any
from uuid import UUID

import structlog
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import text

from olimpo.core.db import session_scope
from olimpo.integrations.actions import (
    ARCHETYPES_BY_PLATFORM,
    default_archetype,
    supported_platforms,
)
from olimpo.integrations.orchestrator import OrchestratorClient

log = structlog.get_logger(__name__)
router = APIRouter(prefix="/v1/nodes", tags=["nós"])


class NodeIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    base_url: str = Field(description="URL do WORKER (porta 8078). Nunca o executor (8077).")
    basic_user: str | None = None
    basic_password: str | None = None
    priority: int = 100
    max_concurrent: int = 4
    notes: str | None = None


class NodePatch(BaseModel):
    base_url: str | None = None
    basic_user: str | None = None
    basic_password: str | None = None
    status: str | None = None
    priority: int | None = None
    max_concurrent: int | None = None
    notes: str | None = None


def _client(row: Any) -> OrchestratorClient:
    return OrchestratorClient(
        base_url=row.base_url,
        basic_user=row.basic_user,
        basic_password=row.basic_password,
        node_name=row.name,
        # Inventário é leitura: consultar um nó não age em conta nenhuma,
        # então não faz sentido silenciar isso no dry-run.
        dry_run=False,
    )


@router.get("")
async def list_nodes() -> dict[str, Any]:
    async with session_scope() as s:
        rows = (await s.execute(text("""
            SELECT n.*,
                   (SELECT count(*) FROM account_placements p
                     WHERE p.node_id = n.id AND p.logged_in) AS accounts_online,
                   jsonb_array_length(n.devices_cache) AS device_count
              FROM orchestrator_nodes n
             ORDER BY n.priority, n.name
        """))).mappings().all()
    return {"nodes": [dict(r) for r in rows]}


@router.post("", status_code=201)
async def create_node(body: NodeIn) -> dict[str, Any]:
    if ":8077" in body.base_url:
        raise HTTPException(
            400,
            "Esta é a porta do executor, que monta o docker.sock e escuta só em "
            "loopback. Use a porta do worker (8078).",
        )
    async with session_scope() as s:
        exists = await s.execute(
            text("SELECT 1 FROM orchestrator_nodes WHERE name = :n"), {"n": body.name}
        )
        if exists.first():
            raise HTTPException(409, f"já existe um nó chamado {body.name!r}")

        row = (await s.execute(text("""
            INSERT INTO orchestrator_nodes
                (name, base_url, basic_user, basic_password, priority, max_concurrent, notes)
            VALUES (:name, :base_url, :basic_user, :basic_password,
                    :priority, :max_concurrent, :notes)
            RETURNING *
        """), body.model_dump())).mappings().one()
    return dict(row)


@router.patch("/{node_id}")
async def update_node(node_id: UUID, body: NodePatch) -> dict[str, Any]:
    fields = {k: v for k, v in body.model_dump().items() if v is not None}
    if not fields:
        raise HTTPException(400, "nada para atualizar")
    sets = ", ".join(f"{k} = :{k}" for k in fields)
    async with session_scope() as s:
        row = (await s.execute(
            text(f"UPDATE orchestrator_nodes SET {sets} WHERE id = :id RETURNING *"),
            {**fields, "id": node_id},
        )).mappings().first()
    if not row:
        raise HTTPException(404, "nó não encontrado")
    return dict(row)


@router.delete("/{node_id}", status_code=204)
async def delete_node(node_id: UUID) -> None:
    async with session_scope() as s:
        await s.execute(
            text("DELETE FROM orchestrator_nodes WHERE id = :id"), {"id": node_id}
        )


@router.post("/{node_id}/probe")
async def probe_node(node_id: UUID, refresh: bool = False) -> dict[str, Any]:
    """Consulta o nó e sincroniza o inventário.

    `refresh=true` força o nó a revarrer os devices — custa ~90s e nunca deve
    ser usado em laço; o serviço já cacheia por 120s.
    """
    async with session_scope() as s:
        node = (await s.execute(
            text("SELECT * FROM orchestrator_nodes WHERE id = :id"), {"id": node_id}
        )).mappings().first()
    if not node:
        raise HTTPException(404, "nó não encontrado")

    client = _client(type("Row", (), dict(node))())
    try:
        accounts, devices, actions = await asyncio.gather(
            client.accounts(refresh), client.devices(refresh), client.actions()
        )
    except Exception as exc:
        async with session_scope() as s:
            await s.execute(text("""
                UPDATE orchestrator_nodes
                   SET status = CASE WHEN consecutive_errors >= 2
                                     THEN 'unreachable'::node_status
                                     ELSE 'degraded'::node_status END,
                       consecutive_errors = consecutive_errors + 1,
                       last_error = :err
                 WHERE id = :id
            """), {"id": node_id, "err": str(exc)[:500]})
        raise HTTPException(502, f"nó inacessível: {exc}") from exc

    acc_list = accounts.get("accounts", [])
    dev_list = devices.get("devices", [])
    catalog = actions.get("actions", {})
    plats = supported_platforms(catalog)

    async with session_scope() as s:
        await s.execute(text("""
            UPDATE orchestrator_nodes
               SET status = 'active', consecutive_errors = 0, last_error = NULL,
                   last_seen_at = now(), inventory_refreshed_at = now(),
                   accounts_cache = CAST(:acc AS jsonb),
                   devices_cache  = CAST(:dev AS jsonb),
                   actions_cache  = CAST(:act AS jsonb),
                   platforms      = CAST(:plats AS text[])
             WHERE id = :id
        """), {
            "id": node_id,
            "acc": _json(acc_list), "dev": _json(dev_list),
            "act": _json(catalog),
            # Um nó só de WhatsApp não pode receber task de Instagram só
            # porque tem device livre.
            "plats": plats,
        })

        # Casa as contas do nó com as contas do Olimpo pelo handle. O
        # orchestrator devolve o username sem "@"; nós guardamos com.
        linked = 0
        for a in acc_list:
            username = a.get("username")
            if not username:
                continue
            res = await s.execute(text("""
                INSERT INTO account_placements
                    (account_id, node_id, container, android_user, logged_in, last_seen_at)
                SELECT id, :node_id, :container, :android_user, :logged_in, now()
                  FROM accounts
                 WHERE handle IN (:h1, :h2)
                ON CONFLICT (account_id, node_id, container) DO UPDATE
                   SET logged_in = EXCLUDED.logged_in, last_seen_at = now()
                RETURNING id
            """), {
                "node_id": node_id,
                "container": a.get("container") or "?",
                "android_user": a.get("user"),
                # No WhatsApp a conta É o user Android e o serviço não reporta
                # logged_in. Ausente não significa deslogada.
                "logged_in": a.get("logged_in") is not False,
                "h1": username, "h2": f"@{username}",
            })
            linked += len(res.fetchall())

    known = await _known_handles()
    discovered = sorted({
        a["username"] for a in acc_list
        if a.get("username") and _handle_for(a) not in known
    })

    return {
        "ok": True,
        "accounts_found": len(acc_list),
        "accounts_linked": linked,
        "devices": len(dev_list),
        "actions": len(catalog),
        "platforms": plats,
        # Contas logadas no nó que o Olimpo ainda não conhece. Elas NÃO são
        # criadas sozinhas: uma conta no Olimpo precisa de persona, arquétipo
        # e nível de autonomia — decisões que só um humano toma.
        "discovered": discovered,
    }


def _handle_for(entry: dict[str, Any]) -> str:
    """Normaliza o identificador do orchestrator para o nosso handle.

    O serviço devolve o username sem "@" no Instagram; no WhatsApp devolve o
    nome do perfil, onde "@" não faz sentido.
    """
    username = (entry.get("username") or "").strip()
    platform = (entry.get("platform") or "instagram").lower()
    return username if platform == "whatsapp" else f"@{username.lstrip('@')}"


async def _known_handles() -> set[str]:
    async with session_scope() as s:
        rows = (await s.execute(text("SELECT handle FROM accounts"))).scalars().all()
    return set(rows)


class ImportIn(BaseModel):
    usernames: list[str] | None = Field(
        default=None, description="Se omitido, importa todas as descobertas."
    )
    #: Só se aplica a contas de rede social. Conta de WhatsApp entra sempre
    #: como `messaging` — ela não tem feed para alimentar.
    archetype: str | None = None


@router.get("/{node_id}/discovered")
async def discovered_accounts(node_id: UUID) -> dict[str, Any]:
    """Contas logadas no nó que ainda não existem no Olimpo."""
    async with session_scope() as s:
        cache = (await s.execute(
            text("SELECT accounts_cache FROM orchestrator_nodes WHERE id = :id"),
            {"id": node_id},
        )).scalar()
    if cache is None:
        raise HTTPException(404, "nó não encontrado")

    known = await _known_handles()
    out: dict[str, dict[str, Any]] = {}
    for entry in cache:
        handle = _handle_for(entry)
        if not entry.get("username") or handle in known:
            continue
        # A mesma conta pode aparecer em vários containers — é uma conta só,
        # com vários lugares onde ela pode operar.
        item = out.setdefault(handle, {
            "handle": handle,
            "username": entry["username"],
            "platform": entry.get("platform", "instagram"),
            "containers": [],
        })
        item["containers"].append(entry.get("container"))
    return {"discovered": list(out.values())}


@router.post("/{node_id}/import")
async def import_accounts(node_id: UUID, body: ImportIn) -> dict[str, Any]:
    """Cria no Olimpo as contas descobertas no nó.

    Nascem em `draft` e L0: existem no sistema e já ficam vinculadas ao
    device, mas não agem até alguém escrever a persona e ativá-las.
    """
    async with session_scope() as s:
        cache = (await s.execute(
            text("SELECT accounts_cache FROM orchestrator_nodes WHERE id = :id"),
            {"id": node_id},
        )).scalar()
    if cache is None:
        raise HTTPException(404, "nó não encontrado")

    wanted = set(body.usernames) if body.usernames else None
    known = await _known_handles()
    created: list[str] = []

    async with session_scope() as s:
        for entry in cache:
            username = entry.get("username")
            if not username or (wanted and username not in wanted):
                continue
            handle = _handle_for(entry)
            platform = (entry.get("platform") or "instagram").lower()
            if platform not in {"instagram", "tiktok", "whatsapp"}:
                continue

            archetype = body.archetype or default_archetype(platform)
            if archetype not in ARCHETYPES_BY_PLATFORM[platform]:
                archetype = default_archetype(platform)

            if handle not in known:
                await s.execute(text("""
                    INSERT INTO accounts (handle, platform, archetype, status, autonomy,
                                          persona_card, notes)
                    VALUES (:handle, CAST(:platform AS platform),
                            CAST(:archetype AS archetype), 'draft', 'L0',
                            '{}'::jsonb, :notes)
                    ON CONFLICT (platform, handle) DO NOTHING
                """), {
                    "handle": handle, "platform": platform,
                    "archetype": archetype,
                    "notes": "importada do inventário do nó — persona ainda não escrita",
                })
                known.add(handle)
                created.append(handle)

            await s.execute(text("""
                INSERT INTO account_placements
                    (account_id, node_id, container, android_user, logged_in, last_seen_at)
                SELECT id, :node_id, :container, :android_user, :logged_in, now()
                  FROM accounts WHERE handle = :handle
                ON CONFLICT (account_id, node_id, container) DO UPDATE
                   SET logged_in = EXCLUDED.logged_in, last_seen_at = now()
            """), {
                "node_id": node_id, "handle": handle,
                "container": entry.get("container") or "?",
                "android_user": entry.get("user"),
                # No WhatsApp a conta É o user Android, então o serviço não
                # reporta logged_in. Ausente não quer dizer deslogada.
                "logged_in": entry.get("logged_in") is not False,
            })

    return {"imported": created, "count": len(created)}


def _json(value: Any) -> str:
    import json
    return json.dumps(value, ensure_ascii=False, default=str)
