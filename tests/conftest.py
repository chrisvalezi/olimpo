"""Configuração comum dos testes.

Regra de ouro: nenhum teste toca serviço externo real. Clientes externos são
mockados com `respx`; o dry-run é forçado independentemente do .env local,
para que rodar a suíte nunca publique nada.
"""

import os

# Precisa vir antes de qualquer import do olimpo — Settings lê no import.
os.environ.setdefault("DISPATCH_ENABLED", "false")
os.environ.setdefault("GLOBAL_KILL_SWITCH", "false")
os.environ.setdefault("OLIMPO_ENV", "development")

import pytest  # noqa: E402

from olimpo.core.config import get_settings  # noqa: E402


@pytest.fixture(autouse=True)
def _no_dispatch():
    """Trava de segurança: se algum teste ligar o dispatch, a suíte falha."""
    settings = get_settings()
    assert settings.dispatch_enabled is False, (
        "DISPATCH_ENABLED está ligado na suíte de testes — isso publicaria em "
        "contas reais. Verifique o .env do ambiente."
    )
    yield
