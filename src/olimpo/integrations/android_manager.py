"""Cliente do android-manager — somente leitura, por disciplina nossa.

O Olimpo NÃO gerencia a frota. Ele observa: saúde dos containers, quais
existem, quais estão vivos. Quem cria, destrói e loga contas é o curador
humano, pelo painel do próprio android-manager.

Três fatos do serviço que moldam este cliente:

1. **O "read-only" não é garantido pelo servidor.** O controle de permissão
   por seção não cobre `/api/devices`, e é justamente ali que moram execução
   de shell arbitrário, injeção de texto e escrita no sdcard. A barreira é a
   ALLOWLIST abaixo — e ela roda antes de a requisição sair.

2. **Não há chave de API.** O token vem de `POST /api/auth/login` e pode
   expirar. O cliente faz login preguiçoso e repete uma vez em caso de 401.

3. **A autenticação pode estar desligada.** Se `authEnabled` for falso, o
   serviço responde tudo como admin, sem credencial. Vale detectar e avisar,
   porque isso significa que qualquer um na rede tem a frota inteira.
"""

from __future__ import annotations

import time
from typing import Any

import httpx
import structlog

from olimpo.integrations.base import BaseClient, IntegrationError

log = structlog.get_logger(__name__)

#: Único conjunto de chamadas que este cliente pode fazer.
#: Tudo o mais levanta ANTES de sair pela rede. Não confiamos no 403 do
#: servidor porque ele não cobre as rotas mais perigosas.
ALLOWLIST: frozenset[tuple[str, str]] = frozenset({
    ("GET", "/api/health"),
    ("GET", "/api/auth/status"),
    ("POST", "/api/auth/login"),
    ("GET", "/api/auth/me"),
    ("GET", "/api/devices"),
    ("GET", "/api/system"),
    ("GET", "/api/config/groups"),
    ("GET", "/api/watchdog/status"),
})

#: Rotas com parâmetro, validadas por prefixo+sufixo.
ALLOWLIST_PATTERNS: tuple[tuple[str, str, str], ...] = (
    ("GET", "/api/devices/", "/users"),
    ("GET", "/api/devices/", "/network"),
    ("GET", "/api/devices/", "/proxy/status"),
    # Captura de tela é POST no serviço, mas é leitura no efeito: não muda
    # nada no device. Entra na lista com timeout curto — ver screenshot().
    ("POST", "/api/devices/", "/screenshot"),
)


class NotAllowed(IntegrationError):
    """Chamada fora da allowlist. Erro de programação, não de rede."""


def _is_allowed(method: str, path: str) -> bool:
    if (method, path) in ALLOWLIST:
        return True
    return any(
        method == m and path.startswith(pre) and path.endswith(suf)
        for m, pre, suf in ALLOWLIST_PATTERNS
    )


class AndroidManagerClient(BaseClient):
    service = "android_manager"

    def __init__(
        self,
        base_url: str,
        *,
        username: str | None = None,
        password: str | None = None,
        verify_tls: bool = True,
        timeout: float = 20.0,
    ) -> None:
        super().__init__(
            base_url=base_url, timeout=timeout,
            # Observar a frota não age em conta nenhuma: o dry-run global
            # não deve cegar o painel.
            dry_run=False, verify_tls=verify_tls,
        )
        self.username = username
        self.password = password
        self._token: str | None = None
        self._auth_enabled: bool | None = None

    # ------------------------------------------------------------------ auth
    async def auth_status(self) -> dict[str, Any]:
        """Diz se o serviço exige credencial. Sem auth, ele responde tudo
        como admin — o que vale um aviso no painel."""
        data = await self._call("GET", "/api/auth/status")
        self._auth_enabled = bool(data.get("authEnabled", True))
        return data

    async def login(self) -> str:
        if not self.username or not self.password:
            raise IntegrationError(
                "android-manager exige usuário e senha: não existe chave de API"
            )
        data = await self._call(
            "POST", "/api/auth/login",
            {"username": self.username, "password": self.password},
            authenticated=False,
        )
        token = data.get("token")
        if not token:
            raise IntegrationError("login sem token na resposta")
        self._token = token
        return token

    # ------------------------------------------------------------- chamadas
    async def health(self) -> dict[str, Any]:
        """Não exige credencial — é a sondagem mais barata que existe."""
        return await self._call("GET", "/api/health", authenticated=False)

    async def devices(self) -> list[dict[str, Any]]:
        """Containers Android vivos.

        O serviço cacheia por ~1 minuto, então consultar com mais frequência
        que isso não traz nada de novo. E um device que morreu há 40s ainda
        aparece vivo aqui — para decisão de despacho, quem sabe mais é o
        orchestrator, que executa.
        """
        data = await self._call("GET", "/api/devices")
        return data if isinstance(data, list) else data.get("devices", [])

    async def system(self) -> dict[str, Any]:
        """Saúde do HOST (CPU/RAM/GPU). Se o host satura, a frota inteira
        degrada — e isso não aparece olhando device por device."""
        return await self._call("GET", "/api/system")

    async def watchdog_status(self) -> dict[str, Any]:
        """Quais devices estão sob vigilância automática do manager.

        Isto é sinal de exclusão: um watchdog que reinicia o container no
        meio de uma publicação nossa quebra a ação e pode custar a conta.
        """
        return await self._call("GET", "/api/watchdog/status")

    async def device_users(self, device_id: str) -> dict[str, Any]:
        """Usuários Android e qual está em primeiro plano.

        É o mais próximo de "qual conta está ativa" que este serviço oferece:
        ele não conhece contas de Instagram, só usuários do sistema.
        """
        return await self._call("GET", f"/api/devices/{device_id}/users")

    async def screenshot(self, device_id: str) -> str:
        """Captura sob demanda. Devolve data-URL pronta para <img>.

        ⚠️ Se o device estiver bootando, o serviço espera o boot antes de
        capturar e a requisição pode segurar por cerca de um minuto. Daí o
        timeout curto: numa varredura de frota, um device ruim envenenaria
        o pool de conexões.
        """
        data = await self._call(
            "POST", f"/api/devices/{device_id}/screenshot", timeout=15.0
        )
        return data.get("image", "")

    # ------------------------------------------------------------- interno
    async def _call(
        self,
        method: str,
        path: str,
        payload: Any = None,
        *,
        authenticated: bool = True,
        timeout: float | None = None,
        _retried: bool = False,
    ) -> Any:
        if not _is_allowed(method, path):
            raise NotAllowed(
                f"{method} {path} fora da allowlist do android-manager. "
                "O Olimpo é observador desta frota, não gestor."
            )
        if not self.base_url:
            raise IntegrationError("android_manager: base_url não configurada")

        if authenticated and self._token is None and self.username:
            await self.login()

        headers = {"Content-Type": "application/json"}
        if authenticated and self._token:
            headers["Authorization"] = f"Bearer {self._token}"

        started = time.monotonic()
        try:
            async with httpx.AsyncClient(
                timeout=timeout or self.timeout, verify=self.verify_tls
            ) as client:
                resp = await client.request(
                    method, f"{self.base_url}{path}", json=payload, headers=headers
                )
        except Exception as exc:
            self.breaker.record_failure()
            raise IntegrationError(f"android_manager {method} {path}: {exc}") from exc

        # Token expirado: um login novo e uma única repetição.
        if resp.status_code == 401 and authenticated and not _retried and self.username:
            self._token = None
            await self.login()
            return await self._call(
                method, path, payload, authenticated=True, timeout=timeout, _retried=True
            )

        if resp.status_code >= 400:
            self.breaker.record_failure()
            raise IntegrationError(
                f"android_manager {method} {path}: HTTP {resp.status_code} "
                f"{resp.text[:200]}"
            )

        self.breaker.record_success()
        log.info(
            "integration.ok", service=self.service, path=path,
            latency_ms=int((time.monotonic() - started) * 1000),
        )
        return resp.json() if resp.content else {}


def normalize_device(raw: dict[str, Any]) -> dict[str, Any]:
    """Extrai o que precisamos de um device, tolerando nomes de campo.

    O esquema do android-manager não é documentado e varia entre versões.
    Em vez de quebrar, procuramos por sinônimos e guardamos o objeto cru em
    `metadata` — campo desconhecido hoje pode ser necessário amanhã.

    Nomes confirmados contra um servidor real em 2026-08-05:
    `name`, `state`, `status`, `adbAddress`, `androidVersion`,
    `screencastDevice`, `supportsNativeVideo`.
    """
    def first(*chaves: str) -> Any:
        for k in chaves:
            if raw.get(k) not in (None, ""):
                return raw[k]
        return None

    container = first("name", "container", "containerName", "id")

    # ⚠️ O serviço reporta `state: "running"`, não `running: true`. Ler o
    # campo errado marca a frota inteira como parada — e um device "parado"
    # não recebe task.
    estado = str(first("state", "status") or "").lower()
    alive = (
        raw.get("running") is True
        or raw.get("alive") is True
        or estado.startswith("running")
        or estado.startswith("up")
    )

    adb = first("adbAddress", "adbTarget", "adb", "address", "ip")
    if adb and ":" not in str(adb):
        adb = f"{adb}:5555"

    video = first("screencastDevice", "videoDevice", "video", "v4l2")
    return {
        "container": container,
        "alive": alive,
        "android_version": first("androidVersion", "android_version", "version"),
        "adb_target": adb,
        "video_device": video,
        # Sem dispositivo de vídeo não há transmissão contínua: o modo
        # alternativo derruba quem já estava assistindo.
        "screencast_capable": bool(video) or raw.get("supportsNativeVideo") is True,
        "metadata": raw,
    }
