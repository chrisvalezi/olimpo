"""Espalhamento anti-detecção.

Estes testes existem porque erro silencioso aqui custa conta banida, não um
bug de tela. Ver docs/06-campanhas.md §5.
"""

from datetime import datetime, timedelta
from uuid import uuid4

import pytest

from olimpo.campaigns.spread import (
    Candidate,
    SpreadError,
    SpreadPolicy,
    plan_slots,
)

WINDOW_START = datetime(2026, 8, 5, 9, 0)
WINDOW_END = datetime(2026, 8, 5, 21, 0)


def make_candidates(n: int, **kwargs) -> list[Candidate]:
    return [
        Candidate(
            account_id=uuid4(),
            handle=f"@conta_{i}",
            active_hours=kwargs.pop("active_hours", (8, 23)),
            **kwargs,
        )
        for i in range(n)
    ]


def test_participacao_e_parcial():
    """Nem toda conta entra em toda campanha: se as mesmas N aparecem sempre
    juntas, o conjunto vira assinatura."""
    candidates = make_candidates(10)
    policy = SpreadPolicy(max_participation=0.5)

    slots, skipped = plan_slots(candidates, policy, WINDOW_START, WINDOW_END, seed=42)

    assert len(slots) <= 5
    assert len(slots) + len(skipped) == 10


def test_amostra_varia_com_o_seed():
    """A seleção é sorteio, não ranking — duas campanhas não pegam sempre as
    mesmas contas."""
    candidates = make_candidates(12)
    policy = SpreadPolicy(max_participation=0.5)

    a, _ = plan_slots(candidates, policy, WINDOW_START, WINDOW_END, seed=1)
    b, _ = plan_slots(candidates, policy, WINDOW_START, WINDOW_END, seed=2)

    assert {s.account_id for s in a} != {s.account_id for s in b}


def test_mesmo_seed_reproduz_o_plano():
    """Determinismo dado o seed: um plano precisa ser auditável e reproduzível."""
    candidates = make_candidates(8)
    policy = SpreadPolicy()

    a, _ = plan_slots(candidates, policy, WINDOW_START, WINDOW_END, seed=7)
    b, _ = plan_slots(candidates, policy, WINDOW_START, WINDOW_END, seed=7)

    assert [(s.account_id, s.at) for s in a] == [(s.account_id, s.at) for s in b]


def test_gap_minimo_entre_contas():
    """Correlação temporal é o sinal mais barato de detectar. Duas contas
    nunca agem coladas."""
    candidates = make_candidates(6)
    policy = SpreadPolicy(jitter_min_minutes=30, jitter_max_minutes=60,
                          max_participation=1.0)

    slots, _ = plan_slots(candidates, policy, WINDOW_START, WINDOW_END, seed=3)

    times = sorted(s.at for s in slots)
    gaps = [(b - a) for a, b in zip(times, times[1:], strict=False)]
    assert all(g >= timedelta(minutes=30) for g in gaps)


def test_respeita_janela_ativa_da_conta():
    """Uma conta que dorme às 23h não comenta às 3h."""
    candidates = make_candidates(3, active_hours=(9, 12))
    policy = SpreadPolicy(jitter_min_minutes=30, jitter_max_minutes=45,
                          max_participation=1.0, respect_active_hours=True)

    slots, _ = plan_slots(candidates, policy, WINDOW_START, WINDOW_END, seed=5)

    assert all(9 <= s.at.hour < 12 for s in slots)


def test_teto_de_acoes_vence_a_campanha():
    """O limite da conta sempre vence a campanha — nunca o contrário."""
    saturated = make_candidates(4, actions_last_hour=99, max_actions_per_hour=12)
    free = make_candidates(4)
    policy = SpreadPolicy(max_participation=1.0)

    slots, skipped = plan_slots(saturated + free, policy, WINDOW_START, WINDOW_END, seed=9)

    chosen = {s.account_id for s in slots}
    assert not chosen & {c.account_id for c in saturated}
    assert any("teto de ações" in reason for _, reason in skipped)


def test_janela_curta_demais_falha_alto():
    """Falhar é melhor que comprimir o jitter: silenciosamente encolher o gap
    é exatamente o que produz o padrão detectável."""
    candidates = make_candidates(20)
    policy = SpreadPolicy(jitter_min_minutes=60, max_participation=1.0)

    with pytest.raises(SpreadError, match="curta demais"):
        plan_slots(candidates, policy, WINDOW_START, WINDOW_START + timedelta(hours=2), seed=1)


def test_politica_invalida_e_rejeitada():
    with pytest.raises(SpreadError):
        SpreadPolicy(max_participation=1.5).validate()
    with pytest.raises(SpreadError):
        SpreadPolicy(jitter_min_minutes=0).validate()
    with pytest.raises(SpreadError):
        SpreadPolicy(jitter_min_minutes=60, jitter_max_minutes=30).validate()


def test_from_json_le_o_formato_do_schema():
    """O formato precisa bater com o que está gravado em campaigns.spread_policy."""
    policy = SpreadPolicy.from_json({
        "jitter_minutes": {"min": 25, "max": 240},
        "max_participation": 0.6,
        "max_accounts_per_target": 12,
        "action_mix": {"like": 1.0, "comment": 0.35},
    })

    assert policy.jitter_min_minutes == 25
    assert policy.jitter_max_minutes == 240
    assert policy.max_participation == 0.6
    assert policy.action_mix == {"like": 1.0, "comment": 0.35}


def test_action_mix_nem_todos_fazem_tudo():
    """Todos que curtiram também comentarem é tão anômalo quanto o inverso."""
    candidates = make_candidates(12)
    policy = SpreadPolicy(
        max_participation=1.0, jitter_min_minutes=5, jitter_max_minutes=15,
        action_mix={"like": 1.0, "comment": 0.3},
    )

    slots, _ = plan_slots(candidates, policy, WINDOW_START, WINDOW_END, seed=11)

    commenters = [s for s in slots if "comment" in s.actions]
    assert 0 < len(commenters) < len(slots)
