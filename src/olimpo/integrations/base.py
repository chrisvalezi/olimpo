"""Base comum dos clientes de serviços externos.

Todo cliente externo tem quatro obrigações (ver CONTRIBUTING.md):
  1. ficar atrás de uma interface (Protocol)
  2. ter modo dry-run  — grava em disco em vez de chamar
  3. retry com backoff — rede falha
  4. timeout explícito — nunca esperar para sempre

Além disso: circuit breaker, para que um serviço caído não vire tempestade
de retry, e registro de toda chamada em `events`.
"""

from __future__ import annotations

import json
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import structlog
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from olimpo.core.config import settings

log = structlog.get_logger(__name__)


class IntegrationError(Exception):
    """Falha ao falar com um serviço externo."""


class CircuitOpen(IntegrationError):
    """O serviço está marcado como fora do ar; nem tentamos."""


class CircuitBreaker:
    """Abre após N falhas seguidas e volta a testar depois de `reset_after`.

    Evita que um serviço fora do ar consuma workers em retry — as tasks
    ficam acumuladas em `scheduled` e nada se perde.
    """

    def __init__(self, threshold: int = 5, reset_after: float = 60.0) -> None:
        self.threshold = threshold
        self.reset_after = reset_after
        self._failures = 0
        self._opened_at: float | None = None

    @property
    def is_open(self) -> bool:
        if self._opened_at is None:
            return False
        if time.monotonic() - self._opened_at >= self.reset_after:
            # meia-abertura: deixa passar uma tentativa
            self._opened_at = None
            self._failures = 0
            return False
        return True

    def record_success(self) -> None:
        self._failures = 0
        self._opened_at = None

    def record_failure(self) -> None:
        self._failures += 1
        if self._failures >= self.threshold:
            self._opened_at = time.monotonic()
            log.warning("circuit_breaker.opened", failures=self._failures)


class BaseClient:
    """Cliente HTTP com dry-run, retry, timeout e circuit breaker."""

    #: nome curto, usado em logs, eventos e no diretório de dry-run
    service: str = "base"

    def __init__(
        self,
        base_url: str,
        token: str = "",
        *,
        timeout: float = 30.0,
        dry_run: bool | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.timeout = timeout
        # O dry-run global vem de DISPATCH_ENABLED; um cliente pode forçar.
        self.dry_run = (not settings.dispatch_enabled) if dry_run is None else dry_run
        self.breaker = CircuitBreaker()

    # ------------------------------------------------------------------ util
    def _headers(self, idempotency_key: str | None = None) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key
        return headers

    def _record_dry_run(self, method: str, path: str, payload: Any) -> dict[str, Any]:
        """Grava o que teria sido enviado e devolve uma resposta sintética."""
        fake_id = f"dryrun-{uuid.uuid4().hex[:12]}"
        entry = {
            "service": self.service,
            "method": method,
            "path": path,
            "payload": payload,
            "at": datetime.now(UTC).isoformat(),
            "external_id": fake_id,
        }
        outdir = Path(settings.dryrun_dir) / self.service
        outdir.mkdir(parents=True, exist_ok=True)
        (outdir / f"{fake_id}.json").write_text(json.dumps(entry, indent=2, default=str))
        log.info("dry_run.recorded", service=self.service, path=path, external_id=fake_id)
        return {"dry_run": True, "id": fake_id, "status": "verified"}

    # ------------------------------------------------------------- requisição
    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=20),
        retry=retry_if_exception_type((httpx.TransportError, httpx.HTTPStatusError)),
        reraise=True,
    )
    async def _raw_request(
        self, method: str, path: str, payload: Any, headers: dict[str, str]
    ) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.request(
                method, f"{self.base_url}{path}", json=payload, headers=headers
            )
            response.raise_for_status()
            return response.json() if response.content else {}

    async def request(
        self,
        method: str,
        path: str,
        payload: Any = None,
        *,
        idempotency_key: str | None = None,
        trace_id: str | None = None,
    ) -> dict[str, Any]:
        if self.dry_run:
            return self._record_dry_run(method, path, payload)

        if not self.base_url:
            raise IntegrationError(f"{self.service}: base_url não configurada")

        if self.breaker.is_open:
            raise CircuitOpen(f"{self.service}: circuito aberto, serviço indisponível")

        started = time.monotonic()
        try:
            result = await self._raw_request(
                method, path, payload, self._headers(idempotency_key)
            )
        except Exception as exc:
            self.breaker.record_failure()
            log.error(
                "integration.failed",
                service=self.service,
                path=path,
                error=str(exc),
                trace_id=trace_id,
            )
            raise IntegrationError(f"{self.service} {method} {path}: {exc}") from exc

        self.breaker.record_success()
        log.info(
            "integration.ok",
            service=self.service,
            path=path,
            latency_ms=int((time.monotonic() - started) * 1000),
            trace_id=trace_id,
        )
        return result
