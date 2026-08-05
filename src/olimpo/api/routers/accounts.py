"""Contas — a lista e a ficha, para o painel."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter, HTTPException
from sqlalchemy import text

from olimpo.core.db import session_scope

router = APIRouter(prefix="/v1/accounts", tags=["contas"])


@router.get("")
async def list_accounts(
    archetype: str | None = None,
    platform: str | None = None,
    status: str | None = None,
) -> dict[str, Any]:
    where, params = [], {}
    if platform:
        where.append("a.platform = CAST(:platform AS platform)")
        params["platform"] = platform
    if archetype:
        where.append("a.archetype = CAST(:archetype AS archetype)")
        params["archetype"] = archetype
    if status:
        where.append("a.status = CAST(:status AS account_status)")
        params["status"] = status
    clause = f"WHERE {' AND '.join(where)}" if where else ""

    async with session_scope() as s:
        rows = (await s.execute(text(f"""
            SELECT a.id, a.handle, a.platform, a.archetype, a.status, a.autonomy,
                   a.followers, a.last_action_at, a.timezone,
                   a.persona_card -> 'identity' ->> 'name'  AS persona_name,
                   a.persona_card -> 'niche'    ->> 'primary' AS niche,
                   -- Onde esta conta está logada. Pode ser em mais de um nó:
                   -- nesse caso a serialização por conta é nossa, porque o
                   -- nó só serializa por device.
                   COALESCE((
                     SELECT jsonb_agg(jsonb_build_object(
                              'node', n.name, 'container', p.container,
                              'logged_in', p.logged_in))
                       FROM account_placements p
                       JOIN orchestrator_nodes n ON n.id = p.node_id
                      WHERE p.account_id = a.id), '[]'::jsonb) AS placements,
                   (SELECT count(*) FROM tasks t
                     WHERE t.account_id = a.id
                       AND t.status IN ('pending','scheduled','dispatched','running')
                   ) AS tasks_open,
                   (SELECT title FROM story_arcs
                     WHERE account_id = a.id AND active LIMIT 1) AS arc
              FROM accounts a {clause}
             ORDER BY a.handle
        """), params)).mappings().all()
    return {"accounts": [dict(r) for r in rows]}


@router.get("/{account_id}")
async def get_account(account_id: UUID) -> dict[str, Any]:
    async with session_scope() as s:
        row = (await s.execute(
            text("SELECT * FROM accounts WHERE id = :id"), {"id": account_id}
        )).mappings().first()
        if not row:
            raise HTTPException(404, "conta não encontrada")

        arcs = (await s.execute(text(
            "SELECT * FROM story_arcs WHERE account_id = :id ORDER BY starts_at DESC LIMIT 10"
        ), {"id": account_id})).mappings().all()
        memories = (await s.execute(text("""
            SELECT kind, content, importance, occurred_at FROM memories
             WHERE account_id = :id ORDER BY occurred_at DESC LIMIT 30
        """), {"id": account_id})).mappings().all()
        tasks = (await s.execute(text("""
            SELECT id, type, status, scheduled_for, evidence_level, last_error
              FROM tasks WHERE account_id = :id ORDER BY created_at DESC LIMIT 30
        """), {"id": account_id})).mappings().all()

    return {
        "account": dict(row),
        "arcs": [dict(a) for a in arcs],
        "memories": [dict(m) for m in memories],
        "tasks": [dict(t) for t in tasks],
    }
