"""ContentSource — a única peça do loop que varia por arquétipo.

Influencer, fofoca e clipagem rodam exatamente o mesmo loop
(PLAN → SOURCE → PRODUCE → QA → DISPATCH → VERIFY → RECORD → REFLECT).
A diferença está aqui, e só aqui: de onde vem o material.

Adicionar um quarto arquétipo deve custar UMA implementação desta interface.
Se estiver exigindo mudança no dispatcher, no QA ou nas campanhas, a
abstração vazou — ver ADR-0006.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol
from uuid import UUID


@dataclass(slots=True)
class Intent:
    """Uma intenção do plano do dia. Ainda não é conteúdo."""

    kind: str                      # publish_post | publish_story | publish_reels
    theme: str
    time_hint: str | None = None
    campaign_id: UUID | None = None
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class PlanContext:
    """Tudo que o agente sabe no momento de produzir."""

    persona_card: dict[str, Any]
    narrative: str
    arc: dict[str, Any] | None
    memories: list[str]
    trends: list[dict[str, Any]]
    campaign_adaptation: str | None = None


@dataclass(slots=True)
class ContentDraft:
    """Saída do SOURCE: mídia resolvida, texto ainda não.

    Os campos de proveniência ficam vazios no arquétipo influencer e são
    obrigatórios em fofoca (source_refs, claim_status) e clipagem
    (attribution, rights_status). O QA cobra.
    """

    kind: str
    media_refs: list[dict[str, Any]]
    source_strategy: str                      # generated | curated | derived
    seed_text: str | None = None              # matéria-prima para o PRODUCE
    source_refs: list[dict[str, Any]] = field(default_factory=list)
    attribution: str | None = None
    rights_status: str = "own"
    claim_status: str = "unverified"
    audio_ref: str | None = None
    reasoning: str = ""


class ContentSource(Protocol):
    """Contrato único das três estratégias."""

    strategy: str

    async def propose(
        self, account_id: UUID, intent: Intent, ctx: PlanContext
    ) -> list[ContentDraft]:
        """Resolve a mídia de uma intenção. Não escreve caption — isso é PRODUCE."""
        ...


class GeneratedSource:
    """Influencer: a mídia é GERADA a partir da vivência fictícia do dia.

    Chama a máquina de influencers com a referência facial fixa da persona.
    Sem essa referência a persona muda de cara entre posts — é o risco nº 1
    do arquétipo, e o erro só aparece depois de publicado.
    """

    strategy = "generated"

    async def propose(
        self, account_id: UUID, intent: Intent, ctx: PlanContext
    ) -> list[ContentDraft]:
        raise NotImplementedError("Fase 1 — ver docs/fases/fase-1-primeira-influencer.md")


class CuratedSource:
    """Fofoca: a mídia é CURADA a partir do que os monitores trouxeram.

    Preenche obrigatoriamente source_refs, attribution e claim_status — o QA
    bloqueia publicação sem eles neste arquétipo.
    """

    strategy = "curated"

    async def propose(
        self, account_id: UUID, intent: Intent, ctx: PlanContext
    ) -> list[ContentDraft]:
        raise NotImplementedError("Fase 4 — ver docs/fases/fase-4-fofoca.md")


class DerivedSource:
    """Clipagem: a mídia é DERIVADA de conteúdo longo, via media-forge."""

    strategy = "derived"

    async def propose(
        self, account_id: UUID, intent: Intent, ctx: PlanContext
    ) -> list[ContentDraft]:
        raise NotImplementedError("Fase 5 — ver docs/fases/fase-5-clipagem.md")


SOURCES: dict[str, type] = {
    "influencer": GeneratedSource,
    "gossip": CuratedSource,
    "clipper": DerivedSource,
}


def source_for(archetype: str) -> ContentSource:
    """Resolve a estratégia pelo arquétipo da conta.

    É o único ponto do loop que sabe que arquétipos existem.
    """
    try:
        return SOURCES[archetype]()  # type: ignore[return-value]
    except KeyError:
        raise ValueError(f"arquétipo sem ContentSource: {archetype}") from None
