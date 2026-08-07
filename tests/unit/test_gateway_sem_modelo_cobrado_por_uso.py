"""A sonda de saúde do gateway não pode alcançar modelo cobrado por chamada.

Este teste existe por causa de um incidente real, e é a trava que o
comentário no YAML sozinho não dá. Ver `docs/incidentes/0001-sonda-de-saude-gerando-imagem.md`.

O mecanismo, em uma frase: `background_health_checks: true` faz o LiteLLM
chamar **de verdade** cada deployment a cada `health_check_interval`
segundos, e num deployment com `mode: image_generation` essa chamada gera
uma imagem cobrada. Dois modelos de imagem no gateway = duas compras por
minuto, dia e noite, sem ninguém pedir nada.

Por que texto continua permitido e imagem não:

    sonda de chat   → alguns tokens, centavos por mês
    sonda de imagem → uma imagem inteira, ~US$ 20/dia por deployment

A diferença não é de grau, é de natureza: a de chat é um ping caro em
tokens; a de imagem é uma compra. Não existe "sonda barata" de geração de
mídia.
"""

from __future__ import annotations

import pathlib

import pytest
import yaml

CONFIG = pathlib.Path(__file__).resolve().parents[2] / "ops" / "litellm.config.yaml"

#: Modos que produzem MÍDIA. A sonda não faz uma chamada simbólica nesses:
#: ela executa a geração inteira, e o provider cobra por ela.
MODOS_QUE_COMPRAM = {
    "image_generation",
    "audio_transcription",
    "audio_speech",
    "video_generation",
    "rerank",
}


@pytest.fixture(scope="module")
def config() -> dict:
    if not CONFIG.exists():  # pragma: no cover
        pytest.skip(f"config do LiteLLM não encontrado em {CONFIG}")
    return yaml.safe_load(CONFIG.read_text()) or {}


def _sonda_ligada(config: dict) -> bool:
    return bool((config.get("general_settings") or {}).get("background_health_checks"))


def test_nenhum_deployment_de_midia_com_a_sonda_ligada(config: dict) -> None:
    """A regra que custou US$ 36 para ser aprendida.

    Se este teste falhar, há duas saídas — e "é só um modelo, vai ser
    barato" **não** é uma delas:

      1. tire o modelo do gateway e chame o provider direto (é o que o
         `media/providers/fal.py` faz, pelo campo `t2i` do registro);
      2. desligue `background_health_checks` e aceite descobrir deployment
         morto no primeiro pedido real.
    """
    if not _sonda_ligada(config):
        pytest.skip("sonda desligada — a regra não se aplica")

    intervalo = (config.get("general_settings") or {}).get("health_check_interval", 60)
    culpados = [
        (d.get("model_name"), (d.get("model_info") or {}).get("mode"))
        for d in (config.get("model_list") or [])
        if (d.get("model_info") or {}).get("mode") in MODOS_QUE_COMPRAM
    ]

    assert not culpados, (
        f"deployment de mídia atrás da sonda de saúde: {culpados}.\n"
        f"Com health_check_interval={intervalo}s isso é "
        f"{86_400 // max(intervalo, 1)} gerações por dia POR DEPLOYMENT, "
        f"sem ninguém pedir nada. Já custou US$ 36 uma vez — ver "
        f"docs/incidentes/0001-sonda-de-saude-gerando-imagem.md"
    )


def test_todo_deployment_declara_o_modo(config: dict) -> None:
    """Sem `mode`, o teste acima não tem o que checar.

    Um deployment de imagem sem `mode` declarado passaria batido aqui e
    ainda seria sondado pelo LiteLLM — que o trataria como chat, falharia, e
    marcaria o modelo como doente. Silencioso nos dois sentidos.
    """
    sem_modo = [
        d.get("model_name") for d in (config.get("model_list") or [])
        if not (d.get("model_info") or {}).get("mode")
    ]
    assert not sem_modo, (
        f"deployment sem `model_info.mode`: {sem_modo}. Declare o modo — é "
        f"o que permite verificar o que a sonda vai executar em cada um."
    )


def test_o_provider_de_imagem_do_gateway_continua_desligado() -> None:
    """O outro lado da mesma decisão.

    Tirar o alias do YAML sem desligar o provider deixaria o código pedindo
    um modelo que não existe mais — erro no worker, não na tela.
    """
    from olimpo.media.providers.base import GenerationRequest, MediaKind
    from olimpo.media.providers.gateway import GatewayImageProvider

    req = GenerationRequest(kind=MediaKind.IMAGE, prompt="qualquer coisa")
    assert not GatewayImageProvider().supports(req)


def test_o_aviso_continua_no_config(config: dict) -> None:
    """O comentário no YAML é onde a próxima pessoa vai ler.

    O teste protege de o aviso sumir numa limpeza — que é como esse tipo de
    conhecimento costuma se perder.
    """
    texto = CONFIG.read_text()
    assert "ANTES DE ADICIONAR QUALQUER MODELO PAGO" in texto, (
        "o aviso sobre a sonda saiu do litellm.config.yaml"
    )
