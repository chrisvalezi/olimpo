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

    async with session_scope() as s:
        await s.execute(text("""
            UPDATE orchestrator_nodes
               SET status = 'active', consecutive_errors = 0, last_error = NULL,
                   last_seen_at = now(), inventory_refreshed_at = now(),
                   accounts_cache = CAST(:acc AS jsonb),
                   devices_cache  = CAST(:dev AS jsonb),
                   actions_cache  = CAST(:act AS jsonb)
             WHERE id = :id
        """), {
            "id": node_id,
            "acc": _json(acc_list), "dev": _json(dev_list),
            "act": _json(actions.get("actions", {})),
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
                "logged_in": bool(a.get("logged_in", True)),
                "h1": username, "h2": f"@{username}",
            })
            linked += len(res.fetchall())

    return {
        "ok": True,
        "accounts_found": len(acc_list),
        "accounts_linked": linked,
        "devices": len(dev_list),
        "actions": len(actions.get("actions", {})),
        # Contas que o nó tem logadas e que o Olimpo ainda não conhece.
        "unlinked": [a.get("username") for a in acc_list][: 50],
    }


def _json(value: Any) -> str:
    import json
    return json.dumps(value, ensure_ascii=False, default=str)
