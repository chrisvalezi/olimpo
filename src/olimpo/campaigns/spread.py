"""Espalhamento anti-detecção.

N contas fazendo a mesma ação na mesma janela é o sinal nº 1 de detecção de
comportamento coordenado. A plataforma não precisa entender o conteúdo —
basta a correlação temporal.

Este módulo é lógica pura e determinística dado um seed: o mesmo plano pode
ser reproduzido e auditado. Ver docs/06-campanhas.md §5 e ADR-0007.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

from olimpo.core.config import settings


class SpreadError(ValueError):
    """Política inválida ou impossível de satisfazer."""


@dataclass(slots=True)
class Candidate:
    """Uma conta elegível para participar da campanha."""

    account_id: UUID
    handle: str
    active_hours: tuple[int, int]      # [início, fim) na timezone da conta
    actions_last_hour: int = 0
    max_actions_per_hour: int = settings.account_max_actions_per_hour
    niche: str | None = None


@dataclass(slots=True)
class Slot:
    """Uma participação sorteada."""

    account_id: UUID
    handle: str
    at: datetime
    actions: list[str]


@dataclass(slots=True)
class SpreadPolicy:
    """O conteúdo de `campaigns.spread_policy`. NOT NULL no schema."""

    jitter_min_minutes: int = settings.campaign_jitter_min_minutes
    jitter_max_minutes: int = settings.campaign_jitter_max_minutes
    max_participation: float = settings.campaign_max_participation
    respect_active_hours: bool = True
    respect_cadence: bool = True
    max_accounts_per_target: int = 12
    min_gap_same_target_minutes: int = 20
    action_mix: dict[str, float] | None = None

    @classmethod
    def from_json(cls, raw: dict[str, Any]) -> SpreadPolicy:
        jitter = raw.get("jitter_minutes", {})
        return cls(
            jitter_min_minutes=jitter.get("min", settings.campaign_jitter_min_minutes),
            jitter_max_minutes=jitter.get("max", settings.campaign_jitter_max_minutes),
            max_participation=raw.get("max_participation", settings.campaign_max_participation),
            respect_active_hours=raw.get("respect_active_hours", True),
            respect_cadence=raw.get("respect_cadence", True),
            max_accounts_per_target=raw.get("max_accounts_per_target", 12),
            min_gap_same_target_minutes=raw.get("min_gap_same_target_minutes", 20),
            action_mix=raw.get("action_mix"),
        )

    def validate(self) -> None:
        if not 0.0 < self.max_participation <= 1.0:
            raise SpreadError("max_participation precisa estar em (0, 1]")
        if self.jitter_min_minutes < 1:
            raise SpreadError("jitter mínimo abaixo de 1 minuto anula o espalhamento")
        if self.jitter_max_minutes < self.jitter_min_minutes:
            raise SpreadError("jitter máximo menor que o mínimo")


def _in_active_hours(moment: datetime, active_hours: tuple[int, int]) -> bool:
    start, end = active_hours
    hour = moment.hour
    # Janela que cruza a meia-noite (ex.: [22, 6))
    return start <= hour < end if start < end else hour >= start or hour < end


def plan_slots(
    candidates: list[Candidate],
    policy: SpreadPolicy,
    window_start: datetime,
    window_end: datetime,
    *,
    seed: int,
) -> tuple[list[Slot], list[tuple[Candidate, str]]]:
    """Sorteia quem participa e quando.

    Devolve (participantes, descartados-com-motivo). Descartar explicitamente
    e com motivo importa: o painel mostra por que uma conta ficou de fora, e
    "ninguém participou" precisa ser distinguível de "ninguém era elegível".
    """
    policy.validate()
    if window_end <= window_start:
        raise SpreadError("janela da campanha inválida")

    rng = random.Random(seed)
    skipped: list[tuple[Candidate, str]] = []

    # 1. Descarta quem já está no teto de ações — o limite da conta vence
    #    a campanha, sempre.
    eligible = []
    for c in candidates:
        if policy.respect_cadence and c.actions_last_hour >= c.max_actions_per_hour:
            skipped.append((c, "teto de ações por hora atingido"))
        else:
            eligible.append(c)

    # 2. Participação parcial. Se as MESMAS N contas aparecem juntas em toda
    #    campanha, o conjunto vira uma assinatura — por isso é sorteio, não
    #    "as N melhores".
    cap = min(
        int(len(eligible) * policy.max_participation),
        policy.max_accounts_per_target,
    )
    if cap < 1 and eligible:
        cap = 1

    rng.shuffle(eligible)
    chosen, rest = eligible[:cap], eligible[cap:]
    skipped.extend((c, "fora da amostra de participação") for c in rest)

    # 3. Distribui os slots pela janela, com distância mínima entre contas.
    total_minutes = int((window_end - window_start).total_seconds() // 60)
    if total_minutes < policy.jitter_min_minutes * max(len(chosen) - 1, 1):
        raise SpreadError(
            f"janela de {total_minutes}min é curta demais para {len(chosen)} contas "
            f"com gap mínimo de {policy.jitter_min_minutes}min — alargue a janela "
            "ou reduza a participação"
        )

    slots: list[Slot] = []
    cursor = window_start
    for candidate in chosen:
        gap = rng.randint(policy.jitter_min_minutes, policy.jitter_max_minutes)
        at = cursor + timedelta(minutes=gap)

        if policy.respect_active_hours:
            # Empurra para a próxima janela em que a conta está "acordada".
            # Uma conta que dorme às 23h não comenta às 3h.
            guard = 0
            while not _in_active_hours(at, candidate.active_hours) and guard < 48:
                at += timedelta(hours=1)
                guard += 1

        if at > window_end:
            skipped.append((candidate, "não coube na janela respeitando o jitter"))
            continue

        slots.append(
            Slot(
                account_id=candidate.account_id,
                handle=candidate.handle,
                at=at,
                actions=_draw_actions(policy, rng),
            )
        )
        cursor = at

    return slots, skipped


def _draw_actions(policy: SpreadPolicy, rng: random.Random) -> list[str]:
    """Sorteia o conjunto de ações desta conta a partir do action_mix.

    Nem todo participante faz tudo. Uma seção de comentários onde todos que
    curtiram também comentaram é tão anômala quanto o inverso — a proporção
    imita o funil natural.
    """
    mix = policy.action_mix or {"like": 1.0}
    return [action for action, prob in mix.items() if rng.random() < prob] or ["like"]
