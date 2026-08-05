"""Auditoria: toda decisão do sistema vira uma linha em `events`.

A pergunta "por que a conta X postou isso às 19h?" precisa continuar
respondível meses depois, atravessando plano do dia → intenção → conteúdo →
raciocínio da LLM → QA → aprovação → task → verificação → post.

Isso é requisito de primeira classe, não logging. Ver docs/11-observabilidade.md.

O `trace_id` amarra tudo: ele nasce no início de um ciclo (plano do dia,
campanha, hit de monitor) e é propagado por toda a cadeia, inclusive nos
headers das chamadas externas.
"""

from __future__ import annotations

import contextvars
import uuid
from typing import Any
from uuid import UUID

import structlog

log = structlog.get_logger(__name__)

#: Propagado automaticamente dentro de um ciclo — não precisa ser passado
#: de função em função.
_trace_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "trace_id", default=None
)


def new_trace(prefix: str = "trc") -> str:
    """Inicia um novo rastro. Chamado no começo de um ciclo."""
    trace_id = f"{prefix}_{uuid.uuid4().hex[:16]}"
    _trace_id.set(trace_id)
    return trace_id


def current_trace() -> str | None:
    return _trace_id.get()


def bind_trace(trace_id: str) -> None:
    """Adota um rastro existente — usado ao processar um webhook que carrega
    o trace_id de quando a task foi despachada."""
    _trace_id.set(trace_id)


class EventType:
    """Catálogo dos tipos de evento.

    Constantes em vez de string solta: um typo em `emit()` produziria um
    evento que nenhuma query encontra, e a falha seria silenciosa.
    """

    # ciclo do agente
    DAY_PLANNED = "agent.day_planned"
    CONTENT_PROPOSED = "agent.content_proposed"
    CONTENT_PRODUCED = "agent.content_produced"
    REFLECTED = "agent.reflected"
    ARC_ADVANCED = "agent.arc_advanced"
    MEMORY_WRITTEN = "agent.memory_written"

    # portão de qualidade
    QA_PASSED = "qa.passed"
    QA_FAILED = "qa.failed"

    # governança
    APPROVAL_REQUESTED = "governance.approval_requested"
    APPROVAL_DECIDED = "governance.approval_decided"
    GUARDRAIL_VIOLATED = "governance.guardrail_violated"
    AUTONOMY_CHANGED = "governance.autonomy_changed"
    KILL_SWITCH = "governance.kill_switch"

    # execução
    TASK_CREATED = "task.created"
    TASK_DISPATCHED = "task.dispatched"
    TASK_VERIFIED = "task.verified"
    TASK_FAILED = "task.failed"
    TASK_EXPIRED = "task.expired"
    TASK_RESCHEDULED = "task.rescheduled"
    DISPATCH_VETOED = "task.dispatch_vetoed"

    # campanhas
    CAMPAIGN_CREATED = "campaign.created"
    RECON_CAPTURED = "campaign.recon_captured"
    RECON_ANALYZED = "campaign.recon_analyzed"
    RECON_BLOCKED = "campaign.recon_blocked"
    CAMPAIGN_PLANNED = "campaign.planned"
    CAMPAIGN_STARTED = "campaign.started"
    CAMPAIGN_COMPLETED = "campaign.completed"
    CAMPAIGN_ABORTED = "campaign.aborted"

    # monitores
    MONITOR_CHECKED = "monitor.checked"
    MONITOR_HIT = "monitor.hit"
    MONITOR_TRIGGERED = "monitor.triggered"
    MONITOR_ERROR = "monitor.error"

    # publicação
    POST_PUBLISHED = "post.published"
    METRICS_COLLECTED = "post.metrics_collected"
    INTERACTION_RECEIVED = "interaction.received"
    INTERACTION_REPLIED = "interaction.replied"
    INTERACTION_ESCALATED = "interaction.escalated"


async def emit(
    event_type: str,
    *,
    account_id: UUID | None = None,
    campaign_id: UUID | None = None,
    task_id: UUID | None = None,
    detail: dict[str, Any] | None = None,
) -> None:
    """Grava um evento com o trace_id corrente.

    Nunca levanta exceção: falha ao auditar não pode derrubar a ação sendo
    auditada. Falha de escrita vira log de erro e alerta.
    """
    trace_id = current_trace()
    payload = detail or {}

    try:
        # TODO(Fase 0): INSERT em `events`. Enquanto a sessão de banco não
        # existe, o log estruturado já preserva o rastro.
        log.info(
            "event",
            event_type=event_type,
            account_id=str(account_id) if account_id else None,
            campaign_id=str(campaign_id) if campaign_id else None,
            task_id=str(task_id) if task_id else None,
            trace_id=trace_id,
            **payload,
        )
    except Exception as exc:  # pragma: no cover
        log.error("event.emit_failed", event_type=event_type, error=str(exc))
