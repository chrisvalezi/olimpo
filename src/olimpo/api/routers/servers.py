"""Servidores, serviços e devices.

Um servidor é um host. Nele podem rodar vários serviços — o orchestrator que
executa ações, o android-manager que gerencia a frota, o gateway que
transmite as telas — cada um com endereço, credencial e TLS próprios.

Nenhum deles conhece os outros, e nenhum conhece a frota inteira. Este
módulo é o índice que falta.
"""

from __future__ import annotations

import asyncio
import json
import time
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
from olimpo.integrations.android_manager import AndroidManagerClient, normalize_device
from olimpo.integrations.orchestrator import OrchestratorClient

log = structlog.get_logger(__name__)
router = APIRouter(prefix="/v1/servers", tags=["servidores"])

#: Porta padrão e caminho de saúde por tipo de serviço. Usado para sugerir
#: o preenchimento na interface — o usuário sempre pode sobrescrever.
DEFAULTS: dict[str, dict[str, Any]] = {
    "orchestrator": {"port": 8078, "health": "/healthz", "scheme": "http",
                     "auth": "basic", "web": ""},
    "android_manager": {"port": 443, "health": "/api/health", "scheme": "https",
                        "auth": "login", "web": "/"},
    "screen_gateway": {"port": 9000, "health": "/", "scheme": "https",
                       "auth": "none", "web": "/"},
    "persona_engine": {"port": 8081, "health": "/", "scheme": "http",
                       "auth": "none", "web": "/"},
    "media_forge": {"port": 8300, "health": "/health", "scheme": "http",
                    "auth": "bearer", "web": ""},
    "custom": {"port": 80, "health": "/", "scheme": "http", "auth": "none", "web": "/"},
}


# =============================================================================
# Modelos
# =============================================================================

class ServerIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    description: str | None = None
    hostname: str | None = None
    tailscale_ip: str | None = None
    lan_ip: str | None = None
    preferred_host: str | None = None
    labels: list[str] = []
    notes: str | None = None


class ServerPatch(BaseModel):
    description: str | None = None
    hostname: str | None = None
    tailscale_ip: str | None = None
    lan_ip: str | None = None
    preferred_host: str | None = None
    status: str | None = None
    labels: list[str] | None = None
    notes: str | None = None


class ServiceIn(BaseModel):
    kind: str
    label: str | None = None
    base_url: str
    web_url: str | None = None
    health_path: str | None = None
    auth_scheme: str = "none"
    auth_user: str | None = None
    auth_secret: str | None = None
    verify_tls: bool = True
    enabled: bool = True
    # Orchestrator apenas
    priority: int = 100
    max_concurrent: int = 4


class ServicePatch(BaseModel):
    label: str | None = None
    base_url: str | None = None
    web_url: str | None = None
    health_path: str | None = None
    auth_scheme: str | None = None
    auth_user: str | None = None
    auth_secret: str | None = None
    verify_tls: bool | None = None
    enabled: bool | None = None


class ImportIn(BaseModel):
    usernames: list[str] | None = None
    archetype: str | None = None


# =============================================================================
# Servidores
# =============================================================================

@router.get("")
async def list_servers() -> dict[str, Any]:
    async with session_scope() as s:
        rows = (await s.execute(text("""
            SELECT sv.*,
                   COALESCE((
                     SELECT jsonb_agg(to_jsonb(x) ORDER BY x.kind)
                       FROM (SELECT ss.id, ss.kind, ss.label, ss.base_url, ss.web_url,
                                    ss.status, ss.enabled, ss.verify_tls, ss.auth_scheme,
                                    ss.last_seen_at, ss.last_error, ss.latency_ms,
                                    ss.version, ss.capabilities
                               FROM server_services ss
                              WHERE ss.server_id = sv.id) x
                   ), '[]'::jsonb) AS services,
                   (SELECT count(*) FROM devices d WHERE d.server_id = sv.id) AS devices_total,
                   (SELECT count(*) FROM devices d WHERE d.server_id = sv.id AND d.alive)
                     AS devices_alive,
                   (SELECT count(*) FROM account_placements p
                      JOIN devices d ON d.id = p.device_id
                     WHERE d.server_id = sv.id AND p.logged_in) AS accounts_online,
                   (SELECT to_jsonb(y) FROM (
                       SELECT ok, accounts_found, accounts_new, accounts_gone,
                              devices_found, duration_ms, error, created_at
                         FROM inventory_syncs
                        WHERE server_id = sv.id ORDER BY created_at DESC LIMIT 1) y
                   ) AS last_sync
              FROM servers sv
             ORDER BY sv.name
        """))).mappings().all()
    return {"servers": [dict(r) for r in rows], "defaults": DEFAULTS}


@router.post("", status_code=201)
async def create_server(body: ServerIn) -> dict[str, Any]:
    async with session_scope() as s:
        if (await s.execute(
            text("SELECT 1 FROM servers WHERE name = :n"), {"n": body.name}
        )).first():
            raise HTTPException(409, f"já existe um servidor chamado {body.name!r}")
        data = body.model_dump()
        data["preferred_host"] = (
            body.preferred_host or body.hostname or body.tailscale_ip or body.lan_ip
        )
        row = (await s.execute(text("""
            INSERT INTO servers (name, description, hostname, tailscale_ip, lan_ip,
                                 preferred_host, labels, notes)
            VALUES (:name, :description, :hostname, :tailscale_ip, :lan_ip,
                    :preferred_host, :labels, :notes)
            RETURNING *
        """), data)).mappings().one()
    return dict(row)


@router.patch("/{server_id}")
async def update_server(server_id: UUID, body: ServerPatch) -> dict[str, Any]:
    fields = {k: v for k, v in body.model_dump().items() if v is not None}
    if not fields:
        raise HTTPException(400, "nada para atualizar")
    sets = ", ".join(
        f"{k} = CAST(:{k} AS server_status)" if k == "status" else f"{k} = :{k}"
        for k in fields
    )
    async with session_scope() as s:
        row = (await s.execute(
            text(f"UPDATE servers SET {sets} WHERE id = :id RETURNING *"),
            {**fields, "id": server_id},
        )).mappings().first()
    if not row:
        raise HTTPException(404, "servidor não encontrado")
    return dict(row)


@router.delete("/{server_id}", status_code=204)
async def delete_server(server_id: UUID) -> None:
    async with session_scope() as s:
        await s.execute(text("DELETE FROM servers WHERE id = :id"), {"id": server_id})


# =============================================================================
# Serviços
# =============================================================================

@router.post("/{server_id}/services", status_code=201)
async def add_service(server_id: UUID, body: ServiceIn) -> dict[str, Any]:
    if body.kind == "orchestrator" and ":8077" in body.base_url:
        raise HTTPException(
            400,
            "A porta 8077 é do executor, que monta o docker.sock e escuta só em "
            "loopback. Use a porta do worker (8078).",
        )
    defaults = DEFAULTS.get(body.kind, DEFAULTS["custom"])
    async with session_scope() as s:
        row = (await s.execute(text("""
            INSERT INTO server_services
                (server_id, kind, label, base_url, web_url, health_path,
                 auth_scheme, auth_user, auth_secret, verify_tls, enabled)
            VALUES (:server_id, CAST(:kind AS service_kind), :label, :base_url,
                    :web_url, :health_path, CAST(:auth_scheme AS auth_scheme),
                    :auth_user, :auth_secret, :verify_tls, :enabled)
            RETURNING *
        """), {
            "server_id": server_id, "kind": body.kind, "label": body.label,
            "base_url": body.base_url.rstrip("/"),
            "web_url": (body.web_url or body.base_url).rstrip("/"),
            "health_path": body.health_path or defaults["health"],
            "auth_scheme": body.auth_scheme, "auth_user": body.auth_user,
            "auth_secret": body.auth_secret, "verify_tls": body.verify_tls,
            "enabled": body.enabled,
        })).mappings().one()

        # O orchestrator carrega estado próprio: inventário, plataformas,
        # capacidade. Por isso tem tabela de extensão.
        if body.kind == "orchestrator":
            await s.execute(text("""
                INSERT INTO orchestrator_nodes
                    (name, base_url, basic_user, basic_password, priority,
                     max_concurrent, server_id, service_id)
                SELECT sv.name || COALESCE('/' || :label, ''), :base_url,
                       :auth_user, :auth_secret, :priority, :max_concurrent,
                       :server_id, :service_id
                  FROM servers sv WHERE sv.id = :server_id
                ON CONFLICT (name) DO UPDATE
                   SET base_url = EXCLUDED.base_url, service_id = EXCLUDED.service_id
            """), {
                "label": body.label, "base_url": body.base_url.rstrip("/"),
                "auth_user": body.auth_user, "auth_secret": body.auth_secret,
                "priority": body.priority, "max_concurrent": body.max_concurrent,
                "server_id": server_id, "service_id": row["id"],
            })
    return dict(row)


@router.patch("/services/{service_id}")
async def update_service(service_id: UUID, body: ServicePatch) -> dict[str, Any]:
    fields = {k: v for k, v in body.model_dump().items() if v is not None}
    if not fields:
        raise HTTPException(400, "nada para atualizar")
    sets = ", ".join(
        f"{k} = CAST(:{k} AS auth_scheme)" if k == "auth_scheme" else f"{k} = :{k}"
        for k in fields
    )
    async with session_scope() as s:
        row = (await s.execute(
            text(f"UPDATE server_services SET {sets} WHERE id = :id RETURNING *"),
            {**fields, "id": service_id},
        )).mappings().first()
        if row and row["kind"] == "orchestrator" and "base_url" in fields:
            await s.execute(
                text("UPDATE orchestrator_nodes SET base_url = :u WHERE service_id = :id"),
                {"u": fields["base_url"], "id": service_id},
            )
    if not row:
        raise HTTPException(404, "serviço não encontrado")
    return dict(row)


@router.delete("/services/{service_id}", status_code=204)
async def delete_service(service_id: UUID) -> None:
    async with session_scope() as s:
        await s.execute(
            text("DELETE FROM server_services WHERE id = :id"), {"id": service_id}
        )


# =============================================================================
# Sondagem
# =============================================================================

@router.post("/{server_id}/probe")
async def probe_server(server_id: UUID, refresh: bool = False) -> dict[str, Any]:
    """Sonda todos os serviços habilitados do servidor, em paralelo.

    Cada um responde por si: um android-manager fora do ar não impede o
    inventário do orchestrator de atualizar.
    """
    async with session_scope() as s:
        server = (await s.execute(
            text("SELECT * FROM servers WHERE id = :id"), {"id": server_id}
        )).mappings().first()
        if not server:
            raise HTTPException(404, "servidor não encontrado")
        services = (await s.execute(text("""
            SELECT * FROM server_services WHERE server_id = :id AND enabled
        """), {"id": server_id})).mappings().all()

    if not services:
        raise HTTPException(400, "servidor sem serviços habilitados")

    results = await asyncio.gather(
        *(_probe_service(dict(sv), server_id, refresh) for sv in services),
        return_exceptions=True,
    )

    out: dict[str, Any] = {}
    for sv, res in zip(services, results, strict=True):
        key = f"{sv['kind']}{'/' + sv['label'] if sv['label'] else ''}"
        out[key] = {"erro": str(res)} if isinstance(res, Exception) else res

    # O servidor está tão saudável quanto o pior dos seus serviços.
    async with session_scope() as s:
        await s.execute(text("""
            UPDATE servers SET status = CASE
                WHEN NOT EXISTS (SELECT 1 FROM server_services
                                  WHERE server_id = :id AND enabled
                                    AND status <> 'online') THEN 'active'
                WHEN EXISTS (SELECT 1 FROM server_services
                              WHERE server_id = :id AND enabled
                                AND status = 'online') THEN 'degraded'
                ELSE 'unreachable' END::server_status
            WHERE id = :id
        """), {"id": server_id})

    return {"server": server["name"], "services": out}


async def _probe_service(
    sv: dict[str, Any], server_id: UUID, refresh: bool
) -> dict[str, Any]:
    started = time.monotonic()
    try:
        if sv["kind"] == "orchestrator":
            result = await _probe_orchestrator(sv, server_id, refresh)
        elif sv["kind"] == "android_manager":
            result = await _probe_android_manager(sv, server_id)
        else:
            result = await _probe_generic(sv)
        status = "online"
        error = None
    except Exception as exc:
        result, status, error = {}, _classify(exc), str(exc)[:500]
        log.warning("probe.failed", kind=sv["kind"], error=error)

    latency = int((time.monotonic() - started) * 1000)
    async with session_scope() as s:
        await s.execute(text("""
            UPDATE server_services
               SET status = CAST(:status AS service_status),
                   latency_ms = :latency,
                   last_seen_at = CASE WHEN :ok THEN now() ELSE last_seen_at END,
                   last_error = :error,
                   consecutive_errors = CASE WHEN :ok THEN 0
                                             ELSE consecutive_errors + 1 END,
                   capabilities = CAST(:caps AS jsonb)
             WHERE id = :id
        """), {
            "id": sv["id"], "status": status, "latency": latency,
            "ok": error is None, "error": error,
            "caps": json.dumps(result.get("capabilities", {}), default=str),
        })

    if error:
        raise RuntimeError(error)
    return {**result, "latency_ms": latency}


def _classify(exc: Exception) -> str:
    """401 e 403 não são "fora do ar" — são credencial errada, e o operador
    precisa ver a diferença para saber o que consertar."""
    texto = str(exc)
    if "401" in texto or "403" in texto or "unauthorized" in texto.lower():
        return "unauthorized"
    return "offline"


async def _probe_generic(sv: dict[str, Any]) -> dict[str, Any]:
    import httpx

    url = f"{sv['base_url']}{sv['health_path']}"
    headers = {}
    if sv["auth_scheme"] == "bearer" and sv["auth_secret"]:
        headers["Authorization"] = f"Bearer {sv['auth_secret']}"
    async with httpx.AsyncClient(timeout=15.0, verify=sv["verify_tls"]) as c:
        resp = await c.get(url, headers=headers)
        resp.raise_for_status()
    return {"ok": True}


async def _probe_android_manager(sv: dict[str, Any], server_id: UUID) -> dict[str, Any]:
    client = AndroidManagerClient(
        sv["base_url"], username=sv["auth_user"], password=sv["auth_secret"],
        verify_tls=sv["verify_tls"],
    )
    await client.health()
    auth = await client.auth_status()

    # Sem credencial só dá para dizer que está vivo — e isso já é útil.
    if not client.username and auth.get("authEnabled", True):
        return {
            "alive": True, "authenticated": False,
            "aviso": "sem credencial: só a checagem de saúde está disponível",
            "capabilities": {"auth_enabled": True, "screenshot": False},
        }

    devices = await client.devices()
    system: dict[str, Any] = {}
    watchdog: dict[str, Any] = {}
    try:
        system = await client.system()
    except Exception:
        pass
    try:
        watchdog = await client.watchdog_status()
    except Exception:
        # Exige permissão de seção; a ausência não é falha do serviço.
        pass

    sob_watchdog = {
        d.get("containerId") or d.get("container") or d.get("id")
        for d in (watchdog.get("devices") or [])
        if d.get("enabled") or d.get("running")
    }

    gravados = 0
    async with session_scope() as s:
        for raw in devices:
            info = normalize_device(raw)
            if not info["container"]:
                continue
            await s.execute(text("""
                INSERT INTO devices (server_id, container, alive, android_version,
                                     adb_target, video_device, screencast_capable,
                                     under_external_management, last_seen_at, metadata)
                VALUES (:server_id, :container, :alive, :android_version, :adb_target,
                        :video_device, :screencast_capable, :managed, now(),
                        CAST(:metadata AS jsonb))
                ON CONFLICT (server_id, container) DO UPDATE SET
                    alive = EXCLUDED.alive,
                    android_version = COALESCE(EXCLUDED.android_version,
                                               devices.android_version),
                    adb_target = COALESCE(EXCLUDED.adb_target, devices.adb_target),
                    video_device = COALESCE(EXCLUDED.video_device, devices.video_device),
                    screencast_capable = EXCLUDED.screencast_capable
                                         OR devices.screencast_capable,
                    under_external_management = EXCLUDED.under_external_management,
                    last_seen_at = now(),
                    metadata = EXCLUDED.metadata
            """), {
                "server_id": server_id, **{k: info[k] for k in (
                    "container", "alive", "android_version", "adb_target",
                    "video_device", "screencast_capable")},
                "managed": info["container"] in sob_watchdog,
                "metadata": json.dumps(info["metadata"], default=str),
            })
            gravados += 1

    com_video = sum(1 for d in devices if normalize_device(d)["screencast_capable"])
    return {
        "alive": True,
        "authenticated": True,
        "devices": gravados,
        "com_transmissao": com_video,
        "sob_watchdog": len(sob_watchdog),
        "host": {k: system.get(k) for k in ("cpu", "memory", "gpu") if k in system},
        "capabilities": {
            "auth_enabled": auth.get("authEnabled", True),
            "screenshot": True,
            "watchdog": bool(watchdog),
            "screencast_devices": com_video,
        },
    }


async def _probe_orchestrator(
    sv: dict[str, Any], server_id: UUID, refresh: bool
) -> dict[str, Any]:
    started = time.monotonic()
    client = OrchestratorClient(
        sv["base_url"], basic_user=sv["auth_user"], basic_password=sv["auth_secret"],
        dry_run=False,
    )
    client.verify_tls = sv["verify_tls"]

    accounts, devices, actions = await asyncio.gather(
        client.accounts(refresh), client.devices(refresh), client.actions()
    )
    acc_list = accounts.get("accounts", [])
    dev_list = devices.get("devices", [])
    catalog = actions.get("actions", {})
    plats = supported_platforms(catalog)

    async with session_scope() as s:
        node = (await s.execute(
            text("SELECT id FROM orchestrator_nodes WHERE service_id = :id"),
            {"id": sv["id"]},
        )).scalar()

        await s.execute(text("""
            UPDATE orchestrator_nodes
               SET status = 'active', consecutive_errors = 0, last_error = NULL,
                   last_seen_at = now(), inventory_refreshed_at = now(),
                   accounts_cache = CAST(:acc AS jsonb),
                   devices_cache  = CAST(:dev AS jsonb),
                   actions_cache  = CAST(:act AS jsonb),
                   platforms      = CAST(:plats AS text[])
             WHERE service_id = :sid
        """), {
            "sid": sv["id"], "acc": json.dumps(acc_list, default=str),
            "dev": json.dumps(dev_list, default=str),
            "act": json.dumps(catalog, default=str), "plats": plats,
        })

        # Devices vistos pelo orchestrator entram no mesmo catálogo que os do
        # android-manager. Quem vê primeiro cria; o outro complementa.
        for d in dev_list:
            if not d.get("container"):
                continue
            await s.execute(text("""
                INSERT INTO devices (server_id, container, alive, last_seen_at)
                VALUES (:server_id, :container, :alive, now())
                ON CONFLICT (server_id, container) DO UPDATE
                   SET alive = EXCLUDED.alive, last_seen_at = now()
            """), {
                "server_id": server_id, "container": d["container"],
                "alive": bool(d.get("alive", True)),
            })

        antes = (await s.execute(text("""
            SELECT count(*) FROM account_placements p
              JOIN devices dv ON dv.id = p.device_id
             WHERE dv.server_id = :sid
        """), {"sid": server_id})).scalar() or 0

        vinculadas = await _sync_placements(s, acc_list, node, server_id)

        depois = (await s.execute(text("""
            SELECT count(*) FROM account_placements p
              JOIN devices dv ON dv.id = p.device_id
             WHERE dv.server_id = :sid
        """), {"sid": server_id})).scalar() or 0

        known = set((await s.execute(text("SELECT handle FROM accounts"))).scalars().all())
        novas = sorted({
            a["username"] for a in acc_list
            if a.get("username") and _handle_for(a) not in known
        })

        await s.execute(text("""
            INSERT INTO inventory_syncs
                (server_id, service_id, kind, ok, accounts_found, accounts_new,
                 accounts_gone, devices_found, duration_ms, forced_refresh)
            VALUES (:server_id, :service_id, 'full', true, :found, :new, :gone,
                    :devices, :ms, :refresh)
        """), {
            "server_id": server_id, "service_id": sv["id"],
            "found": len(acc_list), "new": len(novas),
            "gone": max(antes - depois, 0), "devices": len(dev_list),
            "ms": int((time.monotonic() - started) * 1000), "refresh": refresh,
        })

    return {
        "accounts_found": len(acc_list),
        "accounts_linked": vinculadas,
        "devices": len(dev_list),
        "actions": len(catalog),
        "platforms": plats,
        "discovered": novas,
        "capabilities": {"platforms": plats, "actions": len(catalog)},
    }


async def _sync_placements(
    s: Any, acc_list: list[dict[str, Any]], node_id: Any, server_id: UUID
) -> int:
    """Grava o inventário rico e o lugar de cada conta.

    Antes só o handle sobrevivia à sincronização, e a lista de contas
    mostrava zero seguidores para todo mundo.
    """
    vinculadas = 0
    for a in acc_list:
        username = a.get("username")
        if not username:
            continue
        handle = _handle_for(a)
        # ⚠️ `active` NÃO é "está na tela". O Android mantém vários usuários
        # em execução ao mesmo tempo, e o serviço reporta todos como ativos.
        # Qual está em primeiro plano ele não diz — e trocar de conta é
        # trocar o usuário do device inteiro. Guardamos o que dá para saber.
        user_running = bool(a.get("active"))

        atualizadas = (await s.execute(text("""
            UPDATE accounts SET
                external_id = COALESCE(:external_id, external_id),
                followers   = COALESCE(:followers, followers),
                following   = COALESCE(:following, following),
                media_count = COALESCE(:media_count, media_count),
                jid         = COALESCE(:jid, jid),
                last_synced_at = now()
             WHERE handle = :handle RETURNING id
        """), {
            "handle": handle, "external_id": a.get("id"),
            "followers": a.get("followers"), "following": a.get("following"),
            "media_count": a.get("media"), "jid": a.get("jid"),
        })).fetchall()

        if not atualizadas:
            continue

        await s.execute(text("""
            INSERT INTO account_placements
                (account_id, node_id, device_id, container, android_user, platform,
                 logged_in, foreground, external_id, followers, following,
                 media_count, last_seen_at)
            SELECT a.id, :node_id, dv.id, :container, :android_user, a.platform,
                   :logged_in, :foreground, :external_id, :followers, :following,
                   :media_count, now()
              FROM accounts a
              LEFT JOIN devices dv
                     ON dv.server_id = :server_id AND dv.container = :container
             WHERE a.handle = :handle
            ON CONFLICT (account_id, node_id, container) DO UPDATE SET
                device_id = EXCLUDED.device_id,
                logged_in = EXCLUDED.logged_in,
                foreground = EXCLUDED.foreground,
                external_id = EXCLUDED.external_id,
                followers = EXCLUDED.followers,
                following = EXCLUDED.following,
                media_count = EXCLUDED.media_count,
                android_user = EXCLUDED.android_user,
                last_seen_at = now()
        """), {
            "node_id": node_id, "server_id": server_id,
            "container": a.get("container") or "?", "android_user": a.get("user"),
            # No WhatsApp a conta É o usuário Android, então o serviço não
            # reporta logged_in. Ausente não significa deslogada.
            "logged_in": a.get("logged_in") is not False,
            "foreground": user_running, "external_id": a.get("id"),
            "followers": a.get("followers"), "following": a.get("following"),
            "media_count": a.get("media"), "handle": handle,
        })
        vinculadas += 1
    return vinculadas


def _handle_for(entry: dict[str, Any]) -> str:
    """Identificador do orchestrator → nosso handle.

    Instagram vem sem "@"; no WhatsApp o valor é o nome do perfil, onde "@"
    não faz sentido.
    """
    username = (entry.get("username") or "").strip()
    platform = (entry.get("platform") or "instagram").lower()
    return username if platform == "whatsapp" else f"@{username.lstrip('@')}"


# =============================================================================
# Contas descobertas
# =============================================================================

@router.get("/{server_id}/discovered")
async def discovered(server_id: UUID) -> dict[str, Any]:
    """Contas logadas nos devices do servidor que o Olimpo ainda não conhece."""
    async with session_scope() as s:
        caches = (await s.execute(text("""
            SELECT accounts_cache FROM orchestrator_nodes WHERE server_id = :id
        """), {"id": server_id})).scalars().all()
        known = set((await s.execute(text("SELECT handle FROM accounts"))).scalars().all())

    out: dict[str, dict[str, Any]] = {}
    for cache in caches:
        for entry in cache or []:
            handle = _handle_for(entry)
            if not entry.get("username") or handle in known:
                continue
            item = out.setdefault(handle, {
                "handle": handle, "username": entry["username"],
                "platform": entry.get("platform", "instagram"),
                "external_id": entry.get("id"),
                "followers": entry.get("followers"),
                "containers": [],
            })
            item["containers"].append(entry.get("container"))
    return {"discovered": list(out.values())}


@router.post("/{server_id}/import")
async def import_accounts(server_id: UUID, body: ImportIn) -> dict[str, Any]:
    """Cria no Olimpo as contas descobertas.

    Nascem em `draft`/L0: existem, já ficam vinculadas ao device e trazem o
    inventário real — mas não agem até alguém escrever a persona e ativá-las.
    """
    async with session_scope() as s:
        caches = (await s.execute(text("""
            SELECT accounts_cache FROM orchestrator_nodes WHERE server_id = :id
        """), {"id": server_id})).scalars().all()
        known = set((await s.execute(text("SELECT handle FROM accounts"))).scalars().all())

    entries = [e for cache in caches for e in (cache or []) if e.get("username")]
    wanted = set(body.usernames) if body.usernames else None
    criadas: list[str] = []

    async with session_scope() as s:
        for entry in entries:
            if wanted and entry["username"] not in wanted:
                continue
            handle = _handle_for(entry)
            platform = (entry.get("platform") or "instagram").lower()
            if platform not in ARCHETYPES_BY_PLATFORM:
                continue

            archetype = body.archetype or default_archetype(platform)
            if archetype not in ARCHETYPES_BY_PLATFORM[platform]:
                archetype = default_archetype(platform)

            if handle not in known:
                await s.execute(text("""
                    INSERT INTO accounts (handle, platform, archetype, status, autonomy,
                                          persona_card, external_id, followers, following,
                                          media_count, jid, discovered_from,
                                          last_synced_at, notes)
                    VALUES (:handle, CAST(:platform AS platform),
                            CAST(:archetype AS archetype), 'draft', 'L0', '{}'::jsonb,
                            :external_id, :followers, :following, :media_count, :jid,
                            :server_id, now(),
                            'importada do inventário — persona ainda não escrita')
                    ON CONFLICT (platform, handle) DO NOTHING
                """), {
                    "handle": handle, "platform": platform, "archetype": archetype,
                    "external_id": entry.get("id"),
                    "followers": entry.get("followers") or 0,
                    "following": entry.get("following") or 0,
                    "media_count": entry.get("media") or 0,
                    "jid": entry.get("jid"), "server_id": server_id,
                })
                known.add(handle)
                criadas.append(handle)

        node_id = (await s.execute(
            text("SELECT id FROM orchestrator_nodes WHERE server_id = :id LIMIT 1"),
            {"id": server_id},
        )).scalar()
        await _sync_placements(s, entries, node_id, server_id)

    return {"imported": criadas, "count": len(criadas)}


# =============================================================================
# Devices
# =============================================================================

@router.get("/{server_id}/devices")
async def list_devices(server_id: UUID) -> dict[str, Any]:
    async with session_scope() as s:
        rows = (await s.execute(text("""
            SELECT d.*,
                   COALESCE((
                     SELECT jsonb_agg(jsonb_build_object(
                              'handle', a.handle, 'platform', a.platform,
                              'foreground', p.foreground, 'logged_in', p.logged_in,
                              'android_user', p.android_user))
                       FROM account_placements p JOIN accounts a ON a.id = p.account_id
                      WHERE p.device_id = d.id), '[]'::jsonb) AS accounts
              FROM devices d WHERE d.server_id = :id
             ORDER BY d.container
        """), {"id": server_id})).mappings().all()

        gateway = (await s.execute(text("""
            SELECT base_url, web_url, verify_tls FROM server_services
             WHERE server_id = :id AND kind = 'screen_gateway' AND enabled LIMIT 1
        """), {"id": server_id})).mappings().first()

    return {
        "devices": [dict(r) for r in rows],
        # Sem gateway cadastrado não há transmissão de tela. O painel usa
        # isto para decidir se mostra o botão desligado ou nem mostra.
        "screen_gateway": dict(gateway) if gateway else None,
    }
