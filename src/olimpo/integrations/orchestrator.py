"""Cliente do android-orchestrator — a única porta para o mundo real.

Contrato validado contra o código real do serviço em 2026-08-05.
Ver docs/contratos/android-orchestrator.md.

Três fatos do serviço que moldam este cliente:

1. **São dois processos.** O executor (:8077) monta o docker.sock — quem fala
   com ele tem root no host, e por isso ele escuta só em loopback. O worker
   (:8078) é o cérebro, o agendador e o painel. **Falamos só com o worker.**

2. **Não existe webhook.** O modelo é polling: cria a task, guarda o par
   (nó, id), pergunta o estado. Não há callback_url em lugar nenhum.

3. **Um nó = um host.** Ele descobre containers por `docker ps` local e
   executa por `docker exec` local. Não há índice de frota: a lista de nós é
   do Olimpo, e os ids de task são autoincrement POR NÓ — a chave real é
   (node_id, external_task_id).
"""

from __future__ import annotations

import base64
from typing import Any, Protocol

import structlog

from olimpo.integrations.actions import PlatformMismatch, assert_same_platform
from olimpo.integrations.base import BaseClient

log = structlog.get_logger(__name__)


class ExecutionBackend(Protocol):
    """Interface da camada de execução — permite um fake nos testes."""

    async def create_task(
        self, *, account: str, action: str, params: dict[str, Any],
        scheduled_at: str | None, priority: int,
    ) -> dict[str, Any]: ...

    async def get_task(self, task_id: int | str) -> dict[str, Any]: ...

    async def cancel_task(self, task_id: int | str) -> dict[str, Any]: ...


class OrchestratorClient(BaseClient):
    """Fala com UM nó. Instanciado por nó, a partir de `orchestrator_nodes`."""

    service = "orchestrator"

    def __init__(
        self,
        base_url: str,
        *,
        basic_user: str | None = None,
        basic_password: str | None = None,
        node_name: str = "",
        dry_run: bool | None = None,
    ) -> None:
        super().__init__(base_url=base_url, token="", timeout=45.0, dry_run=dry_run)
        self.node_name = node_name or base_url
        # Auth é opt-in e pode diferir por nó: um pode estar aberto e outro
        # exigir Basic. Por isso as credenciais são opcionais.
        self._basic = (
            base64.b64encode(f"{basic_user}:{basic_password}".encode()).decode()
            if basic_user
            else None
        )

    def _headers(self, idempotency_key: str | None = None) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self._basic:
            headers["Authorization"] = f"Basic {self._basic}"
        return headers  # o serviço não tem chave de idempotência — ver dedupe()

    # ------------------------------------------------------------- inventário
    async def health(self) -> bool:
        try:
            await self.request("GET", "/healthz")
            return True
        except Exception:
            return False

    async def accounts(self, refresh: bool = False) -> dict[str, Any]:
        """Contas logadas neste nó.

        `refresh=1` roda `list-accounts` em cada device: ~90s. Nunca chamar
        em laço de polling — o serviço já cacheia por 120s.
        """
        return await self.request("GET", f"/accounts{'?refresh=1' if refresh else ''}")

    async def devices(self, refresh: bool = False) -> dict[str, Any]:
        return await self.request("GET", f"/devices{'?refresh=1' if refresh else ''}")

    async def actions(self) -> dict[str, Any]:
        """Catálogo de ações DESTE nó.

        Nós podem rodar versões diferentes do código: validar params contra
        este catálogo, nunca contra uma lista fixa no nosso código.
        """
        return await self.request("GET", "/actions")

    # ---------------------------------------------------------------- despacho
    async def create_task(
        self,
        *,
        account: str,
        action: str,
        params: dict[str, Any],
        platform: str | None = None,
        scheduled_at: str | None = None,
        priority: int = 5,
        trace_id: str | None = None,
    ) -> dict[str, Any]:
        """Cria uma task no nó. Sem `scheduled_at`, dispara quase imediato.

        Duas invariantes que o serviço não valida e que, se violadas, deixam
        a task presa em `pending` para sempre sem nenhum erro visível:
          • `action` precisa vir QUALIFICADA ("instagram.post-feed")
          • `params` não pode ser vazio — no mínimo {"account": ...}
        """
        if "." not in action:
            raise ValueError(
                f"ação não qualificada: {action!r} — o serviço exige 'plataforma.ação'"
            )
        if platform:
            # Última barreira: a ação pode ter sido montada por outro caminho
            # (retry, replay, painel), e é aqui que ela vira efeito real.
            assert_same_platform(platform, action)
        body = {
            "action": action,
            "account": account,
            "params": {"account": account, **params},
            "priority": max(0, min(10, priority)),
        }
        if scheduled_at:
            # Horário de Brasília, UTC-3 fixo. O serviço não usa timezone.
            body["scheduled_at"] = scheduled_at
        return await self.request("POST", "/tasks", body, trace_id=trace_id)

    async def get_task(self, task_id: int | str) -> dict[str, Any]:
        """Estado da task + `sessions[]` (um por tentativa, mais recente primeiro)."""
        return await self.request("GET", f"/tasks/{task_id}")

    async def get_job(self, job_id: str) -> dict[str, Any]:
        """Resultado REAL da execução: `ok`, `data`, `error`, `exit_code`.

        O status da task diz que terminou; é aqui que se descobre se a ação
        teve efeito.
        """
        return await self.request("GET", f"/jobs/{job_id}")

    async def cancel_task(self, task_id: int | str) -> dict[str, Any]:
        """⚠️ Responde 303 com HTML e ENGOLE exceções — um erro aqui não vira
        status de erro. Sempre confirmar relendo get_task()."""
        return await self.request("POST", f"/tasks/{task_id}/cancel")

    async def task_log(self, task_id: int | str) -> dict[str, Any]:
        return await self.request("GET", f"/tasks/{task_id}/log")

    async def upload(self, filename: str) -> dict[str, Any]:
        """Uploads são POR NÓ: um vídeo enviado ao nó A não existe no nó B.

        A URL devolvida deve ser repassada como veio — ela é escolhida para
        ser alcançável de dentro do device. Reescrever o host quebra.
        """
        return await self.request("POST", f"/upload?name={filename}")


# =============================================================================
# Mapeamento de ações → olimpo.integrations.actions
# =============================================================================
# O registro é POR PLATAFORMA e vive em actions.py. Não existe mapa global de
# propósito: pedir uma ação sem dizer a plataforma é erro de programação, e
# `instagram.post-feed` despachado para uma conta de WhatsApp é aceito pelo
# serviço e morre no device sem sinal nenhum.

def build_params(platform: str, task_type: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Traduz o payload interno para os params do orchestrator.

    Recebe a plataforma de propósito: `send_dm` do Instagram e `send_message`
    do WhatsApp têm formatos diferentes, e adivinhar pelo nome do tipo é como
    esses erros começam.
    """
    if platform == "whatsapp":
        return _whatsapp_params(task_type, payload)
    if platform == "tiktok":
        raise PlatformMismatch("TikTok ainda não é suportado pelo orchestrator")

    if task_type == "publish_post":
        return {
            "media": payload.get("media_refs", []),
            "caption": _sanitize(payload.get("caption", "")),
            # Sem publish=true o serviço monta tudo e PARA na tela de legenda.
            "publish": payload.get("publish", True),
            "capture_id": True,   # tenta devolver o shortcode do post criado
        }
    if task_type == "publish_story":
        return {
            "media": payload.get("media_refs", [None])[0],
            "caption": _sanitize(payload.get("caption", "")),
            "publish": payload.get("publish", True),
        }
    if task_type == "publish_reels":
        return {
            "video": payload.get("media_refs", [None])[0],
            "caption": _sanitize(payload.get("caption", "")),
            "publish": payload.get("publish", True),
            "audio_mode": payload.get("audio_mode", "original"),
        }
    if task_type in {"like", "save"}:
        return {"target": payload.get("target_url")}
    if task_type == "comment":
        return {"target": payload.get("target_url"), "text": _sanitize(payload.get("text", ""))}
    if task_type == "reply_comment":
        return {
            "target": payload.get("target_url"),
            "username": payload.get("target_handle"),
            "texts": [_sanitize(payload.get("text", ""))],
        }
    if task_type in {"follow", "unfollow"}:
        return {"target": payload.get("target_handle")}
    if task_type == "send_dm":
        return {"target": payload.get("target_handle"), "text": _sanitize(payload.get("text", ""))}
    if task_type == "watch_reels":
        return {
            "source": payload.get("source", "reels"),
            "query": payload.get("segment"),
            "count": payload.get("count", 10),
            "watch_min": payload.get("min_seconds", 8),
            "watch_max": payload.get("max_seconds", 25),
        }
    if task_type == "view_stories":
        return {"count": payload.get("count", 10)}
    if task_type == "extract_content":
        return {"target": payload.get("target_url"), "no_video": True}
    if task_type == "collect_metrics":
        return {"target": payload.get("target_handle")}
    return dict(payload)


def _whatsapp_params(task_type: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Params do WhatsApp.

    O alvo aqui é um telefone ou um nome de contato — nunca um @handle. E o
    conteúdo vai para uma PESSOA REAL: reenvio duplicado não é um post
    repetido, é alguém recebendo a mesma mensagem duas vezes.
    """
    alvo = payload.get("target_phone") or payload.get("target_handle")

    if task_type in {"send_message", "reply_message"}:
        return {"to": alvo, "text": _sanitize(payload.get("text", ""))}
    if task_type == "send_file":
        return {"to": alvo, "file": payload.get("file_ref"),
                "caption": _sanitize(payload.get("caption", ""))}
    if task_type == "send_voice":
        return {"to": alvo, "file": payload.get("file_ref")}
    if task_type == "forward_message":
        return {"to": alvo, "message_id": payload.get("message_id")}
    if task_type in {"read_messages", "chat_summary", "mark_read", "archive_chat"}:
        return {"chat": alvo}
    if task_type == "read_inbox":
        return {"limit": payload.get("limit", 20)}
    if task_type == "add_contact":
        return {"phone": alvo, "name": payload.get("name")}
    if task_type == "send_poll":
        return {"to": alvo, "question": _sanitize(payload.get("text", "")),
                "options": payload.get("options", [])}
    return dict(payload)


def _sanitize(text: str) -> str:
    """Remove tokens iniciados por `--`.

    O autodroid trunca o texto no primeiro token `--…` e não há escape: uma
    caption gerada por LLM que contenha "--" perde tudo dali em diante, ou
    o serviço recusa com 400. Hífen simples passa.
    """
    return " ".join(w for w in text.split() if not w.startswith("--"))


def read_evidence(job: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """Interpreta o resultado de um job → (evidence_level, evidence).

    Hierarquia:
      verified — o nó confirmou pelo EFEITO na tela (data.verified/published/sent)
      reported — exit 0, mas a ação não produz prova
      unknown  — timeout/restart no meio: PODE ter acontecido
      none     — falhou
    """
    status = (job.get("status") or "").lower()
    data = job.get("data") or {}

    if status == "unknown":
        return "unknown", {"reason": "timeout ou restart durante a execução", **data}
    if not job.get("ok"):
        return "none", {"error": job.get("error"), "exit_code": job.get("exit_code")}

    proof = {k: data[k] for k in ("verified", "published", "sent", "saved", "state", "result")
             if k in data}
    if proof:
        confirmed = any(
            v is True or v in {"liked", "already_liked"} for v in proof.values()
        )
        return ("verified" if confirmed else "reported"), {**proof, **data}

    return "reported", data


def to_task_status(external_status: str) -> str:
    """Estado da task no nó → tasks.status.

    `unknown` NUNCA vira `failed`: uma postagem que estourou o tempo depois
    do toque em Compartilhar pode ter publicado. Vai para verificação humana.
    """
    return {
        "pending": "dispatched",
        "running": "running",
        "done": "verified",
        "failed": "failed",
        "canceled": "cancelled",
        "cancelled": "cancelled",
        "unknown": "running",
    }.get(external_status.lower(), "running")
