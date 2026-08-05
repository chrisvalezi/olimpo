"""Contratos dos motores de prompt.

Estes motores são o ativo intelectual absorvido da máquina de influencers:
biblioteca de guarda-roupa mapeada por energia de personalidade, arquétipos
de profissão que travam a cena, física de luz por horário. Nada disso é
gerado por LLM — é conhecimento de domínio afinado empiricamente, e o porte
precisa preservar a semântica caractere a caractere.

Por isso existe teste de regressão contra os prompts guardados no banco: se
o motor portado reproduz o que a origem produziu, o porte está correto.

⚠️ Aleatoriedade: os motores originais usam `Math.random()` em vários pontos
(escolha de guarda-roupa entre os 6 melhores, prop, cena candid). O porte usa
um `random.Random` explícito, para que o mesmo seed reproduza o mesmo prompt
— coisa que a origem não conseguia fazer.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

Genero = Literal["Female", "Male"]

#: Posição da referência no array → tag no prompt. A ordem É o contrato:
#: quem vem primeiro é o rosto, o segundo é a roupa. O modelo não entende
#: "@image2", então a tag é traduzida para "the second reference image" —
#: e se a ordem do array mudar, a tradução aponta para a imagem errada.
ORDEM_REFERENCIAS = ("face", "wardrobe", "close_up_face", "close_up_detail")


@dataclass(slots=True)
class Persona:
    """O que os motores de prompt precisam saber de uma persona.

    Deliberadamente menor que a `personas` do banco: o motor não deve
    depender de nada que não influencie a imagem.
    """

    name: str
    gender: Genero = "Female"
    apparent_age: int | None = None
    #: Entra literalmente no bloco de sujeito quando não há referência facial.
    physical_desc: str = ""
    backstory: str = ""
    #: 0–100. Define a janela de energia do guarda-roupa, a pose e o modo
    #: como a roupa é usada. Não é decorativo.
    personality_energy: int = 50
    niches: list[str] = field(default_factory=list)
    vibe_words: list[str] = field(default_factory=list)
    clothing_style: str = ""
    #: Classificação feita por LLM (tags de estilo + nicho de cena). Pode
    #: sobrescrever as tags do arquétipo, mas nunca a cena travada.
    backstory_context: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class Referencia:
    """Uma imagem que entra no array, com a tag que o prompt usa."""

    papel: str          # face | wardrobe | close_up_face | close_up_detail | product
    tag: str            # "@image1"
    url: str


@dataclass(slots=True)
class PromptMontado:
    """Saída de um motor: o texto e o que o acompanha.

    As referências vêm juntas de propósito — separá-las do texto é como a
    ordem se perde.
    """

    texto: str
    referencias: list[Referencia] = field(default_factory=list)
    aspect_ratio: str = "9:16"
    #: Registrado para auditoria: por que este prompt ficou assim.
    escolhas: dict[str, Any] = field(default_factory=dict)

    @property
    def urls_das_referencias(self) -> list[str]:
        """O array na ordem exata em que o provider deve recebê-lo."""
        return [r.url for r in self.referencias]
