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
# Mapeamento de ações
# =============================================================================
#: task_type do Olimpo → ação qualificada no orchestrator.
#: Validado contra o SPEC real do serviço.
ACTION_MAP: dict[str, str] = {
    "publish_post": "instagram.post-feed",
    "publish_story": "instagram.post-story",
    "publish_reels": "instagram.post-reel",
    "like": "instagram.like",
    "comment": "instagram.comment",
    "follow": "instagram.follow",
    "unfollow": "instagram.unfollow",
    "save": "instagram.save-post",
    "watch_reels": "instagram.watch-reels",
    "view_stories": "instagram.watch-stories",
    "send_dm": "instagram.dm",
    "reply_comment": "instagram.reply",
    # Recon: a única primitiva de extração real. Só funciona com REELS e
    # NÃO traz o texto dos comentários — ver docs/06-campanhas.md §3.
    "extract_content": "instagram.reel-download",
    "collect_metrics": "instagram.profile-stats",
}

#: Sem equivalente no serviço hoje. Pedir estes tipos é erro de programação,
#: não falha de runtime — falhe cedo, no planejamento.
UNSUPPORTED: dict[str, str] = {
    "share": (
        "não existe compartilhar post de feed. Só reels, via "
        "instagram.reel-action do=[share|story|repost] ou instagram.repost-reel"
    ),
    "collect_interactions": (
        "não existe leitura de comentários ou DMs recebidas. "
        "profile-stats traz contadores, não conteúdo"
    ),
}

#: Ações que NÃO podem ser repetidas cegamente. `like` é toggle: retry
#: DESCURTE. Publicação repetida duplica post.
NON_RETRYABLE: frozenset[str] = frozenset({
    "instagram.post-feed", "instagram.post-reel", "instagram.post-story",
    "instagram.repost-reel", "instagram.like", "instagram.comment",
    "instagram.comments", "instagram.reply", "instagram.engage",
    "instagram.dm", "instagram.reply-story", "instagram.watch-reels",
    "instagram.watch-stories", "instagram.follow-followers",
    "instagram.reel-action", "instagram.remove-account",
})

#: Ações que produzem prova de efeito (data.verified/published/sent).
#: As demais devolvem data vazio: `ok` é tudo o que se tem.
HAS_EVIDENCE: frozenset[str] = frozenset({
    "instagram.post-feed", "instagram.post-reel", "instagram.post-story",
    "instagram.dm", "instagram.save-post", "instagram.follow",
    "instagram.unfollow", "instagram.reel-action",
})


def build_params(task_type: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Traduz o payload interno para os params do orchestrator.

    Um ponto único de tradução: mudança no contrato externo não se espalha.
    """
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
