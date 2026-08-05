"""Configuração central. Tudo vem de variável de ambiente — ver .env.example."""

from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    # --- ambiente ---------------------------------------------------------
    olimpo_env: Literal["development", "staging", "production"] = "development"
    log_level: str = "INFO"

    # Interruptor mestre: com False nada é enviado ao android-orchestrator;
    # as ações são gravadas em dryrun/. Padrão seguro.
    dispatch_enabled: bool = False
    # Parada total: todos os agentes ficam inertes.
    global_kill_switch: bool = False

    # --- infra ------------------------------------------------------------
    database_url: str = "postgresql+asyncpg://olimpo:olimpo@postgres:5432/olimpo"
    redis_url: str = "redis://redis:6379/0"

    secret_key: str = ""
    api_token: str = ""

    # --- LLM --------------------------------------------------------------
    llm_gateway_url: str = "http://litellm:4000"
    litellm_master_key: str = ""
    llm_daily_budget_usd: float = 5.0

    # --- serviços externos ------------------------------------------------
    orchestrator_url: str = ""
    orchestrator_token: str = ""
    orchestrator_webhook_url: str = ""
    orchestrator_webhook_secret: str = ""

    persona_engine_url: str = ""
    persona_engine_token: str = ""

    android_manager_url: str = ""
    android_manager_token: str = ""

    media_forge_url: str = ""
    media_forge_token: str = ""

    # --- operação ---------------------------------------------------------
    worker_concurrency: int = 8

    # Anti-detecção. Ver docs/06-campanhas.md §5.
    campaign_jitter_min_minutes: int = 25
    campaign_jitter_max_minutes: int = 240
    campaign_max_participation: float = Field(default=0.6, ge=0.0, le=1.0)
    account_max_actions_per_hour: int = 12

    l2_sampling_rate: float = Field(default=0.10, ge=0.0, le=1.0)

    # Caminho onde o modo dry-run grava o que teria sido publicado.
    dryrun_dir: str = "/app/dryrun"

    @property
    def sync_database_url(self) -> str:
        """URL síncrona — o runner de migrações usa psycopg, não asyncpg."""
        return self.database_url.replace("+asyncpg", "")

    @property
    def is_production(self) -> bool:
        return self.olimpo_env == "production"


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
