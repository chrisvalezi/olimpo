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
        "olimpo.workers.tasks.produce_*": {"queue": "production"},
        "olimpo.workers.tasks.recon_*": {"queue": "production"},
        "olimpo.workers.tasks.reflect_*": {"queue": "maintenance"},
        "olimpo.workers.tasks.collect_*": {"queue": "maintenance"},
        "olimpo.workers.tasks.reconcile_*": {"queue": "maintenance"},
    },
)

app.conf.beat_schedule = {
    # O tick que move o sistema: busca tasks vencidas e despacha.
    "dispatcher-tick": {
        "task": "olimpo.workers.tasks.dispatch_due_tasks",
        "schedule": 30.0,
    },
    # Rede falha e webhook se perde: varre tasks presas em voo.
    "reconciler": {
        "task": "olimpo.workers.tasks.reconcile_inflight",
        "schedule": 300.0,
    },
    # Monitores 24/7 vencidos (Fase 4).
    "monitor-tick": {
        "task": "olimpo.workers.tasks.monitor_due_checks",
        "schedule": 30.0,
    },
    # Plano do dia de cada conta, no início da janela ativa dela.
    "daily-planning": {
        "task": "olimpo.workers.tasks.plan_accounts_day",
        "schedule": 900.0,
    },
    # Reflexão noturna: sintetiza o dia em insights e avança o arco.
    "nightly-reflection": {
        "task": "olimpo.workers.tasks.reflect_accounts",
        "schedule": 3600.0,
    },
    # Métricas dos posts recentes (janelas de 1h, 24h, 7d).
    "metrics-collection": {
        "task": "olimpo.workers.tasks.collect_recent_metrics",
        "schedule": 1800.0,
    },
}

# As implementações chegam na Fase 0/1 — ver docs/fases/
# from olimpo.workers import tasks  # noqa: E402,F401
