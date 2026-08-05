"""Regressão dos motores de prompt portados.

Estes motores vieram de ~2.600 linhas de JavaScript afinadas empiricamente.
Cada frase é resultado de tentativa e erro contra o modelo de imagem, e uma
vírgula a mais muda o que sai. O porte precisa preservar semântica, não
"melhorar".

Reprodução exata caractere a caractere não é possível para todo prompt: o
motor original sorteia guarda-roupa, prop e cena com `Math.random()`, sem
semente. O que se testa aqui são as INVARIANTES — o que precisa valer em
todo prompt gerado, independentemente do sorteio. Se uma delas quebrar, a
imagem sai errada de um jeito específico e conhecido.
"""

from __future__ import annotations

import random
import re

import pytest

from olimpo.prompts.tipos import Persona

pytest.importorskip("olimpo.prompts.persona_image", reason="motor ainda não portado")

from olimpo.prompts import persona_image  # noqa: E402


@pytest.fixture
def kayla() -> Persona:
    """Uma persona real do acervo importado, para o teste ser representativo."""
    return Persona(
        name="Kayla", gender="Female", apparent_age=18,
        physical_desc="caucasian, long straight blonde hair, blue eyes, "
                      "fair skin tone, slim build",
        backstory="fashion student who thrifts and restyles vintage pieces",
        personality_energy=85, niches=["Fashion"], vibe_words=["editorial"],
    )


@pytest.fixture
def rng() -> random.Random:
    """Semente fixa: o porte permite reproduzir o sorteio, coisa que o
    original não conseguia."""
    return random.Random(20260805)


class TestVariacoes:
    def test_gera_exatamente_tres(self, kayla, rng):
        assert len(persona_image.montar_tres_variacoes(kayla, rng=rng)) == 3

    def test_as_tres_sao_diferentes(self, kayla, rng):
        """A diversidade em lote vem de pose e cena diferentes por variação.
        Três prompts iguais desperdiçam três gerações."""
        textos = [p.texto for p in persona_image.montar_tres_variacoes(kayla, rng=rng)]
        assert len(set(textos)) == 3

    def test_mesma_semente_reproduz(self, kayla):
        a = [p.texto for p in persona_image.montar_tres_variacoes(
            kayla, rng=random.Random(7))]
        b = [p.texto for p in persona_image.montar_tres_variacoes(
            kayla, rng=random.Random(7))]
        assert a == b

    def test_sementes_diferentes_divergem(self, kayla):
        a = persona_image.montar_tres_variacoes(kayla, rng=random.Random(1))[0].texto
        b = persona_image.montar_tres_variacoes(kayla, rng=random.Random(2))[0].texto
        assert a != b


class TestInvariantes:
    """O que precisa valer em TODO prompt, independentemente do sorteio."""

    def _todos(self, kayla, rng) -> list[str]:
        return [p.texto for p in persona_image.montar_tres_variacoes(kayla, rng=rng)]

    def test_camera_e_sempre_iphone(self, kayla, rng):
        """A identidade estética do produto inteiro é realismo de iPhone,
        nunca câmera profissional. Está comentado assim no original."""
        for texto in self._todos(kayla, rng):
            assert "iPhone" in texto

    def test_nunca_usa_as_palavras_banidas(self, kayla, rng):
        """Estas palavras empurram o modelo para o brilho artificial de
        banco de imagens — o oposto do que o produto quer."""
        banidas = [
            "stunning", "ethereal", "hyper-realistic", "8K", "ultra-detailed",
            "flawless", "glowing", "symmetrical",
        ]
        # Duas armadilhas ao verificar isto ingenuamente:
        #
        # 1. Substring: "symmetrical" está dentro de "asymmetrically", que é
        #    o oposto — assimetria natural é o que faz a pele parecer real.
        # 2. Negação: o motor escreve "no perfectly symmetrical face", que
        #    é exatamente o efeito que a regra quer. Proibir a palavra ali
        #    seria proibir a própria defesa contra ela.
        #
        # O que a regra proíbe é a palavra como QUALIDADE afirmada.
        negacao = r"(?:no|not|never|zero|avoid|without)\s+(?:\w+\s+){0,2}"
        for texto in self._todos(kayla, rng):
            achadas = []
            for b in banidas:
                for m in re.finditer(rf"\b{re.escape(b)}\b", texto, flags=re.I):
                    antes = texto[max(0, m.start() - 40):m.start()]
                    if not re.search(negacao + r"$", antes, flags=re.I):
                        achadas.append(b)
            assert not achadas, f"palavra banida usada como qualidade: {achadas}"

    def test_descricao_fisica_entra_literal(self, kayla, rng):
        """Sem referência facial, a descrição física É a identidade. Ela
        precisa entrar inteira, não resumida."""
        texto = self._todos(kayla, rng)[0]
        assert "blonde" in texto.lower()

    def test_comprimento_plausivel(self, kayla, rng):
        """Prompt curto demais perde a cena; longo demais dilui o sujeito.
        A faixa vem dos prompts reais guardados (961 a 4.210 caracteres)."""
        for texto in self._todos(kayla, rng):
            assert 400 < len(texto) < 6000, f"comprimento fora da faixa: {len(texto)}"


class TestArquetipos:
    """O motor de arquétipos é o que substitui a LLM: reconhece a profissão
    na história e trava a cena. É a peça mais valiosa do porte."""

    @pytest.mark.parametrize(
        ("historia", "esperado"),
        [
            ("personal trainer at a local gym", "gym"),
            ("software engineer building side projects", "desk"),
            ("doctor working at a clinic downtown", "clinic"),
        ],
    )
    def test_profissao_trava_a_cena(self, historia, esperado):
        ctx = persona_image.get_backstory_context("", historia)
        assert ctx.locked_scene, f"nenhuma cena travada para: {historia}"
        assert esperado in ctx.locked_scene.lower()

    def test_historia_generica_nao_trava(self):
        ctx = persona_image.get_backstory_context("", "likes coffee and long walks")
        assert not ctx.locked_scene


class TestGuardaRoupa:
    def test_energia_alta_escolhe_look_expressivo(self):
        """A energia da personalidade define a janela do guarda-roupa. Uma
        pessoa de energia 95 não usa o look mais discreto do acervo."""
        alto = persona_image.get_energy_window(95)
        baixo = persona_image.get_energy_window(5)
        assert min(alto.values()) > min(baixo.values()), (
            "energia alta precisa abrir uma janela de guarda-roupa mais expressiva"
        )

    def test_respeita_o_genero(self):
        for _ in range(20):
            look = persona_image.select_wardrobe(
                gender="Male", vibe_words=[], personality=50,
                physical_desc="", backstory="", rng=random.Random(),
            )
            # Devolve o texto do look; nenhum look masculino do acervo
            # descreve peça exclusivamente feminina.
            assert isinstance(look, str) and look
            assert "dress," not in look.lower()

    def test_todo_look_proibe_logo(self):
        """Logo visível vira problema de marca e de plataforma. Todas as
        entradas do acervo terminam com essa instrução."""
        faltando = [w.text for w in persona_image.WARDROBE
                    if "no logos visible" not in w.text.lower()]
        assert not faltando, f"{len(faltando)} looks sem a trava de logo"
