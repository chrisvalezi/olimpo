"""Celery do Olimpo.

Quatro filas por prioridade. Quando os Mac minis saturam, a degradação é
intencional e nesta ordem: adia-se `maintenance`, depois `production`.
`realtime` nunca é adiada — um seguidor esperando resposta é custo de
reputação; um plano do dia atrasado 20 minutos não é.

O agendamento NÃO vive aqui. A fonte da verdade é tasks.scheduled_for no
Postgres; o beat abaixo só dá o tick que acorda o dispatcher (ADR-0004).
"""

from celery import Celery

from olimpo.core.config import settings

app = Celery("olimpo", broker=settings.redis_url, backend=settings.redis_url)

app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="America/Sao_Paulo",
    enable_utc=True,
    task_acks_late=True,              # não perder task se o worker morrer
    worker_prefetch_multiplier=1,     # justiça entre filas com prioridade
    task_track_started=True,
    result_expires=86400,
    task_default_queue="production",
    task_routes={
        "olimpo.workers.tasks.verify_*": {"queue": "realtime"},
        "olimpo.workers.tasks.reply_*": {"queue": "realtime"},
        "olimpo.workers.tasks.monitor_*": {"queue": "realtime"},
        "olimpo.workers.tasks.dispatch_*": {"queue": "dispatch"},
        "olimpo.workers.tasks.plan_*": {"queue": "production"},
        "olimpo.workers.tasks.produce_reconcile_*": {"queue": "maintenance"},
        "olimpo.workers.tasks.produce_*": {"queue": "production"},
        "olimpo.workers.tasks.recon_*": {"queue": "production"},
        "olimpo.workers.tasks.reflect_*": {"queue": "maintenance"},
        "olimpo.workers.tasks.collect_*": {"queue": "maintenance"},
        "olimpo.workers.tasks.reconcile_*": {"queue": "maintenance"},
    },
)

app.conf.beat_schedule = {
    # Resgata jobs de geração presos em `running` — worker morto no meio.
    "reconciliar-geracao": {
        "task": "olimpo.workers.tasks.produce_reconcile_generation",
        "schedule": 600.0,
    },
}

# ⚠️ Agendamento só entra aqui quando a task EXISTE. O beat envia de qualquer
# jeito e o worker rejeita com KeyError a cada tick, enchendo o log de ruído
# que esconde erro de verdade. As entradas abaixo esperam a implementação:
#
#   dispatcher-tick      olimpo.workers.tasks.dispatch_due_tasks       30s
#   reconciler           olimpo.workers.tasks.reconcile_inflight      300s
#   monitor-tick         olimpo.workers.tasks.monitor_due_checks       30s   (Fase 4)
#   daily-planning       olimpo.workers.tasks.plan_accounts_day       900s
#   nightly-reflection   olimpo.workers.tasks.reflect_accounts       3600s
#   metrics-collection   olimpo.workers.tasks.collect_recent_metrics 1800s

from olimpo.workers import tasks_geracao  # noqa: E402,F401

# As demais implementações chegam na Fase 0/1 — ver docs/fases/
# from olimpo.workers import tasks  # noqa: E402,F401
