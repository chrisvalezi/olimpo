"""API do Olimpo — consumida pelo painel e pelos webhooks do orchestrator."""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from starlette.responses import Response

from olimpo.core.config import settings
from olimpo.api.routers import accounts, nodes, servers
from olimpo.core.db import healthcheck

log = structlog.get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):  # type: ignore[no-untyped-def]
    log.info(
        "olimpo.starting",
        env=settings.olimpo_env,
        dispatch_enabled=settings.dispatch_enabled,
        kill_switch=settings.global_kill_switch,
    )
    if not settings.dispatch_enabled:
        log.warning(
            "dry_run.active",
            msg="DISPATCH_ENABLED=false — nada será enviado ao orchestrator",
            dir=settings.dryrun_dir,
        )
    yield
    log.info("olimpo.stopping")


app = FastAPI(
    title="Olimpo",
    description="Core de agentes sociais autônomos",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"] if not settings.is_production else [],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health", tags=["sistema"])
async def health() -> dict[str, Any]:
    """Usado pelo healthcheck do Docker e pelo painel.

    Os dois interruptores são expostos de propósito: o painel mostra uma
    faixa de aviso quando o sistema está em dry-run ou parado, e essa
    informação muda o significado de tudo o que aparece na tela.
    """
    db_ok = await healthcheck()
    return {
        "status": "ok" if db_ok else "degraded",
        "env": settings.olimpo_env,
        "database": "ok" if db_ok else "unreachable",
        "dispatch_enabled": settings.dispatch_enabled,
        "kill_switch": settings.global_kill_switch,
    }


@app.get("/metrics", tags=["sistema"], include_in_schema=False)
async def metrics() -> Response:
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


app.include_router(servers.router)
app.include_router(nodes.router)
app.include_router(accounts.router)

# Routers das próximas fases — ver docs/fases/
# campaigns, approvals, tasks, events (SSE)
#
# Não haverá router de webhook: o android-orchestrator não tem callback.
# O caminho é polling — ver ADR-0011.
