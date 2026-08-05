"""Cliente do android-orchestrator — a única porta para o mundo real.

Nenhuma outra parte do Olimpo fala com Instagram/TikTok/WhatsApp. Toda ação
vira uma task despachada aqui, e só conta como feita quando o orchestrator
CONFIRMA a execução no device (webhook, com polling de fallback).

⚠️ Os endpoints e payloads abaixo refletem o contrato ASSUMIDO. Validar
contra a API real — ver docs/contratos/android-orchestrator.md.
"""

from __future__ import annotations

from typing import Any, Protocol
from uuid import UUID

import structlog

from olimpo.core.config import settings
from olimpo.integrations.base import BaseClient

log = structlog.get_logger(__name__)


class ExecutionBackend(Protocol):
    """Interface da camada de execução. Permite trocar o backend (ou usar um
    fake nos testes) sem que o core saiba a diferença."""

    async def dispatch(
        self, *, account_handle: str, action: str, payload: dict[str, Any],
        idempotency_key: str,
    ) -> dict[str, Any]: ...

    async def get_status(self, external_task_id: str) -> dict[str, Any]: ...

    async def cancel(self, external_task_id: str) -> dict[str, Any]: ...


class OrchestratorClient(BaseClient):
    service = "orchestrator"

    def __init__(self) -> None:
        super().__init__(
            base_url=settings.orchestrator_url,
            token=settings.orchestrator_token,
            timeout=45.0,
        )

    async def dispatch(
        self,
        *,
        account_handle: str,
        action: str,
        payload: dict[str, Any],
        idempotency_key: str,
        trace_id: str | None = None,
    ) -> dict[str, Any]:
        """Envia uma ação para execução em device real.

        Devolve pelo menos {"id": <external_task_id>, "status": ...}.
        A idempotency_key é o id da nossa task: reenviar a mesma task após
        timeout não pode gerar duas publicações.
        """
        body = {
            "account": account_handle,
            "action": action,
            "params": payload,
            "callback_url": settings.orchestrator_webhook_url or None,
        }
        return await self.request(
            "POST", "/v1/tasks", body,
            idempotency_key=idempotency_key, trace_id=trace_id,
        )

    async def get_status(self, external_task_id: str) -> dict[str, Any]:
        """Polling de fallback — usado pelo reconciliador quando o webhook
        não chegou. Nunca é o caminho primário."""
        return await self.request("GET", f"/v1/tasks/{external_task_id}")

    async def cancel(self, external_task_id: str) -> dict[str, Any]:
        """⚠️ A CONFIRMAR: não se sabe ainda se o orchestrator expõe
        cancelamento de task já aceita (pergunta 16 do contrato).

        Se não expuser, o kill switch não consegue abortar o que já foi
        delegado — o que reforça manter o agendamento do nosso lado e
        despachar o mais tarde possível. Ver ADR-0004.
        """
        return await self.request("POST", f"/v1/tasks/{external_task_id}/cancel")

    # --------------------------------------------------------------- recon --
    async def extract_content(
        self,
        *,
        collector_handle: str,
        target_url: str,
        want_comments: int = 10,
        trace_id: str | None = None,
    ) -> dict[str, Any]:
        """RECON: abre um conteúdo alheio e extrai o que ele é.

        É o que permite planejar campanha de engajamento sem comentar às
        cegas. Ver docs/06-campanhas.md §3.
        """
        return await self.request(
            "POST",
            "/v1/tasks",
            {
                "account": collector_handle,
                "action": "extract_content",
                "params": {
                    "url": target_url,
                    "capture_media": True,
                    "capture_comments": want_comments,
                },
            },
            idempotency_key=f"recon:{target_url}",
            trace_id=trace_id,
        )

    async def find_account(self, handle: str) -> dict[str, Any]:
        """Localiza em qual device a conta está logada. Consultado antes do
        dispatch para falhar cedo quando a conta não está disponível."""
        return await self.request("GET", f"/v1/accounts/{handle}")


#: Mapeia o task_type do Olimpo para o nome da ação no orchestrator.
#: ⚠️ A CONFIRMAR contra a API real.
ACTION_MAP: dict[str, str] = {
    "publish_post": "post_feed",
    "publish_story": "post_story",
    "publish_reels": "post_reels",
    "like": "like_post",
    "comment": "comment_post",
    "follow": "follow_user",
    "unfollow": "unfollow_user",
    "save": "save_post",
    "share": "share_post",
    "watch_reels": "watch_reels",
    "view_stories": "view_stories",
    "send_dm": "send_dm",
    "reply_comment": "reply_comment",
    "extract_content": "extract_content",
    "collect_metrics": "collect_metrics",
    "collect_interactions": "collect_interactions",
    "monitor_check": "extract_content",
}


def build_payload(task_type: str, task_payload: dict[str, Any]) -> dict[str, Any]:
    """Traduz o payload interno para o formato esperado pelo orchestrator.

    Mantido em um único lugar para que uma mudança no contrato externo não
    se espalhe pelo core.
    """
    if task_type in {"publish_post", "publish_reels"}:
        return {
            "media": task_payload.get("media_refs", []),
            "caption": task_payload.get("caption", ""),
            "hashtags": task_payload.get("hashtags", []),
            "audio": task_payload.get("audio_ref"),
        }
    if task_type == "publish_story":
        return {
            "media": task_payload.get("media_refs", []),
            "stickers": task_payload.get("stickers", []),
        }
    if task_type in {"comment", "reply_comment"}:
        return {"url": task_payload.get("target_url"), "text": task_payload.get("text", "")}
    if task_type in {"like", "save", "share"}:
        return {"url": task_payload.get("target_url")}
    if task_type in {"follow", "unfollow"}:
        return {"handle": task_payload.get("target_handle")}
    if task_type == "watch_reels":
        return {
            "segment": task_payload.get("segment"),
            "count": task_payload.get("count", 10),
            "min_seconds": task_payload.get("min_seconds", 8),
        }
    if task_type == "send_dm":
        return {"handle": task_payload.get("target_handle"), "text": task_payload.get("text", "")}
    return task_payload


def to_task_status(external_status: str) -> str:
    """Mapeia o status externo para tasks.status.

    Estado desconhecido vira 'running' de propósito: manter a task em voo e
    deixar o reconciliador decidir é mais seguro do que assumir sucesso ou
    falha — assumir falha causa republicação duplicada.
    """
    return {
        "queued": "dispatched",
        "pending": "dispatched",
        "running": "running",
        "in_progress": "running",
        "done": "verified",
        "success": "verified",
        "verified": "verified",
        "failed": "failed",
        "error": "failed",
        "cancelled": "cancelled",
    }.get(external_status.lower(), "running")
