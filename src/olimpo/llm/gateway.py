"""Cliente do LLM Gateway (LiteLLM).

O core nunca fala com Ollama nem com a API da Anthropic diretamente — tudo
passa pelo gateway, que faz balanceamento entre os dois Mac minis, fallback
e contabilidade. Ver docs/09-llm-gateway.md e ADR-0010.

Toda chamada:
  • escolhe o alias certo pela finalidade (não pelo nome do modelo)
  • valida a saída contra um modelo Pydantic — LLM não devolve texto solto
  • grava custo e latência em llm_calls
"""

from __future__ import annotations

import json
import time
from enum import StrEnum
from typing import Any, TypeVar

import httpx
import structlog
from pydantic import BaseModel, ValidationError

from olimpo.core.config import settings

log = structlog.get_logger(__name__)

T = TypeVar("T", bound=BaseModel)


class Purpose(StrEnum):
    """Finalidade da chamada. Define o roteamento e vira llm_calls.purpose."""

    PLAN_DAY = "plan_day"
    REFLECT = "reflect"
    CAPTION = "caption"
    IMAGE_PROMPT = "image_prompt"
    CLASSIFY_INTERACTION = "classify_interaction"
    REPLY_INTERACTION = "reply_interaction"
    RECON_ANALYSIS = "recon_analysis"
    CAMPAIGN_ADAPTATION = "campaign_adaptation"
    INGEST_RELEVANCE = "ingest_relevance"
    CLIP_DETECTION = "clip_detection"
    QA_TEXT = "qa_text"
    MEMORY_IMPORTANCE = "memory_importance"


#: Alias do gateway por finalidade.
#: `olimpo-fast`   → LLM local, alto volume, baixa latência
#: `olimpo-reason` → raciocínio; pode escalar para API externa
#: `olimpo-vision` → multimodal (recon)
ROUTING: dict[Purpose, str] = {
    Purpose.PLAN_DAY: "olimpo-reason",
    Purpose.REFLECT: "olimpo-reason",
    Purpose.CAPTION: "olimpo-fast",
    Purpose.IMAGE_PROMPT: "olimpo-fast",
    Purpose.CLASSIFY_INTERACTION: "olimpo-fast",
    Purpose.REPLY_INTERACTION: "olimpo-fast",
    Purpose.RECON_ANALYSIS: "olimpo-vision",
    Purpose.CAMPAIGN_ADAPTATION: "olimpo-reason",
    Purpose.INGEST_RELEVANCE: "olimpo-fast",
    Purpose.CLIP_DETECTION: "olimpo-reason",
    Purpose.QA_TEXT: "olimpo-fast",
    Purpose.MEMORY_IMPORTANCE: "olimpo-fast",
}


class LLMError(Exception):
    """Falha na chamada ou saída que não valida após as tentativas."""


class LLMGateway:
    def __init__(self, base_url: str | None = None) -> None:
        self.base_url = (base_url or settings.llm_gateway_url).rstrip("/")
        self.key = settings.litellm_master_key

    async def complete(
        self,
        *,
        purpose: Purpose,
        system: str,
        user: str,
        schema: type[T],
        images: list[str] | None = None,
        temperature: float = 0.8,
        max_retries: int = 2,
        trace_id: str | None = None,
        account_id: str | None = None,
    ) -> T:
        """Chama a LLM e devolve a saída já validada em `schema`.

        Na falha de validação, reenvia incluindo o erro — modelo pequeno erra
        JSON com frequência, e uma segunda tentativa com o erro em mãos
        costuma resolver sem precisar escalar para um modelo maior.
        """
        model = ROUTING[purpose]
        content: Any = user
        if images:
            content = [{"type": "text", "text": user}] + [
                {"type": "image_url", "image_url": {"url": ref}} for ref in images
            ]

        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": content},
        ]

        last_error: str | None = None
        for attempt in range(max_retries + 1):
            if last_error:
                messages.append(
                    {
                        "role": "user",
                        "content": (
                            f"Sua resposta anterior não validou: {last_error}\n"
                            "Responda somente com JSON válido no formato pedido."
                        ),
                    }
                )

            started = time.monotonic()
            raw, usage = await self._call(model, messages, temperature, schema)
            latency_ms = int((time.monotonic() - started) * 1000)

            try:
                parsed = schema.model_validate_json(raw)
            except ValidationError as exc:
                last_error = str(exc)[:400]
                log.warning(
                    "llm.validation_failed",
                    purpose=purpose,
                    attempt=attempt,
                    trace_id=trace_id,
                )
                continue

            await self._record(
                purpose=purpose, model=model, usage=usage,
                latency_ms=latency_ms, success=True,
                trace_id=trace_id, account_id=account_id,
            )
            return parsed

        await self._record(
            purpose=purpose, model=model, usage={}, latency_ms=0,
            success=False, error=last_error,
            trace_id=trace_id, account_id=account_id,
        )
        raise LLMError(f"{purpose}: saída inválida após {max_retries + 1} tentativas")

    async def _call(
        self, model: str, messages: list[dict[str, Any]],
        temperature: float, schema: type[BaseModel],
    ) -> tuple[str, dict[str, Any]]:
        body = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": schema.__name__, "schema": schema.model_json_schema()},
            },
        }
        headers = {"Content-Type": "application/json"}
        if self.key:
            headers["Authorization"] = f"Bearer {self.key}"

        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await client.post(
                f"{self.base_url}/v1/chat/completions", json=body, headers=headers
            )
            resp.raise_for_status()
            data = resp.json()

        return data["choices"][0]["message"]["content"], data.get("usage", {})

    async def _record(
        self, *, purpose: Purpose, model: str, usage: dict[str, Any],
        latency_ms: int, success: bool, error: str | None = None,
        trace_id: str | None = None, account_id: str | None = None,
    ) -> None:
        """Grava a chamada em llm_calls (custo, latência, backend).

        TODO(Fase 0): persistir no banco. Por ora, log estruturado — o
        Prometheus já consegue agregar a partir daqui.
        """
        log.info(
            "llm.call",
            purpose=str(purpose), model=model,
            prompt_tokens=usage.get("prompt_tokens"),
            output_tokens=usage.get("completion_tokens"),
            latency_ms=latency_ms, success=success, error=error,
            trace_id=trace_id, account_id=account_id,
        )


gateway = LLMGateway()


def json_instruction(schema: type[BaseModel]) -> str:
    """Bloco de formato para anexar ao prompt.

    O `response_format` do gateway cobre os backends que o suportam; este
    bloco é o cinto de segurança para os modelos locais que ignoram o campo.
    """
    return (
        "Responda SOMENTE com um JSON válido neste formato, sem texto antes "
        f"ou depois:\n{json.dumps(schema.model_json_schema(), ensure_ascii=False)}"
    )
