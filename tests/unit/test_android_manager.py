"""Cliente do android-manager.

O Olimpo é observador desta frota, não gestor. A garantia disso é a
allowlist deste cliente — e ela precisa ser garantia de verdade, porque o
controle de permissão do serviço NÃO cobre as rotas mais perigosas: sob
`/api/devices` moram execução de shell arbitrário, injeção de digitação e
escrita no sdcard, todas acessíveis a qualquer usuário autenticado.
"""

import pytest

from olimpo.integrations.android_manager import (
    ALLOWLIST,
    AndroidManagerClient,
    NotAllowed,
    _is_allowed,
    normalize_device,
)


class TestAllowlist:
    def test_leituras_conhecidas_passam(self):
        for m, p in [
            ("GET", "/api/health"),
            ("GET", "/api/devices"),
            ("GET", "/api/system"),
            ("GET", "/api/watchdog/status"),
            ("POST", "/api/auth/login"),
        ]:
            assert _is_allowed(m, p), f"{m} {p} deveria passar"

    def test_rotas_com_parametro_passam(self):
        assert _is_allowed("GET", "/api/devices/samsung-1/users")
        assert _is_allowed("POST", "/api/devices/samsung-1/screenshot")

    @pytest.mark.parametrize(
        ("metodo", "path"),
        [
            # Execução arbitrária de código no device
            ("POST", "/api/devices/x/command"),
            ("POST", "/api/automation/run"),
            ("POST", "/api/watchdog/test-command/x"),
            # Destrói ou reconfigura infraestrutura
            ("DELETE", "/api/containers/x"),
            ("POST", "/api/containers/x/restart"),
            ("POST", "/api/kernel-modules/apply"),
            ("POST", "/api/deploy/run"),
            # Muda a conta em uso — o estrago mais silencioso de todos
            ("POST", "/api/devices/x/users/10/switch"),
            ("POST", "/api/devices/x/input/text"),
            # Desliga a autenticação do serviço inteiro
            ("PUT", "/api/auth/settings"),
        ],
    )
    def test_rotas_perigosas_sao_recusadas(self, metodo, path):
        assert not _is_allowed(metodo, path)

    def test_metodo_errado_na_rota_certa_e_recusado(self):
        """`GET /api/devices` é leitura; `POST /api/devices` cria coisa."""
        assert _is_allowed("GET", "/api/devices")
        assert not _is_allowed("POST", "/api/devices")
        assert not _is_allowed("DELETE", "/api/devices")

    def test_allowlist_nao_tem_escrita_alem_do_login_e_captura(self):
        escritas = {(m, p) for m, p in ALLOWLIST if m != "GET"}
        assert escritas == {("POST", "/api/auth/login")}

    @pytest.mark.asyncio
    async def test_chamada_fora_da_lista_levanta_antes_da_rede(self):
        client = AndroidManagerClient("http://x:4000")
        with pytest.raises(NotAllowed, match="observador desta frota"):
            await client._call("POST", "/api/devices/abc/command", {"command": "rm -rf /"})


#: Objeto real devolvido por GET /api/devices, capturado de um servidor
#: em 2026-08-05. Os nomes de campo aqui são a razão de este teste existir.
DEVICE_REAL = {
    "id": "e40dd1114a65",
    "ip": "172.17.0.7",
    "name": "samsung-galaxy-a54-5g-4",
    "image": "christianvalezi/redroid-pro:15",
    "state": "running",
    "status": "Up 2 hours",
    "adbAddress": "172.17.0.7:5555",
    "androidVersion": "15",
    "screencastDevice": "/dev/video5",
    "supportsNativeVideo": True,
}


class TestDeviceReal:
    """Regressão: a primeira versão lia `running` e `videoDevice`, que não
    existem nesta API. O resultado foi a frota inteira marcada como parada e
    sem tela — e device parado não recebe task."""

    def test_estado_vem_de_state_e_nao_de_running(self):
        assert normalize_device(DEVICE_REAL)["alive"] is True
        assert normalize_device({"name": "x", "state": "exited"})["alive"] is False

    def test_status_textual_tambem_serve(self):
        assert normalize_device({"name": "x", "status": "Up 2 hours"})["alive"] is True

    def test_dispositivo_de_video_vem_de_screencastdevice(self):
        d = normalize_device(DEVICE_REAL)
        assert d["video_device"] == "/dev/video5"
        assert d["screencast_capable"] is True

    def test_campos_completos(self):
        d = normalize_device(DEVICE_REAL)
        assert d["container"] == "samsung-galaxy-a54-5g-4"
        assert d["adb_target"] == "172.17.0.7:5555"
        assert d["android_version"] == "15"


class TestNormalizacao:
    def test_reconhece_nomes_alternativos(self):
        """O esquema do serviço não é documentado e varia entre versões:
        quebrar por causa de um nome de campo seria frágil demais."""
        a = normalize_device({"name": "pixel-1", "running": True})
        b = normalize_device({"container": "pixel-1", "alive": True})
        assert a["container"] == b["container"] == "pixel-1"
        assert a["alive"] and b["alive"]

    def test_completa_a_porta_adb(self):
        d = normalize_device({"name": "x", "ip": "172.17.0.9"})
        assert d["adb_target"] == "172.17.0.9:5555"

    def test_nao_duplica_porta_ja_presente(self):
        d = normalize_device({"name": "x", "adbTarget": "172.17.0.9:5555"})
        assert d["adb_target"] == "172.17.0.9:5555"

    def test_sem_video_nao_transmite(self):
        """Sem dispositivo de vídeo só resta o modo que derruba quem já
        estava assistindo — inútil para uma grade de telas."""
        assert normalize_device({"name": "x"})["screencast_capable"] is False
        assert normalize_device(
            {"name": "x", "videoDevice": "/dev/video3"}
        )["screencast_capable"] is True

    def test_preserva_o_objeto_cru(self):
        """Campo desconhecido hoje pode ser necessário amanhã."""
        raw = {"name": "x", "campoNovo": 42}
        assert normalize_device(raw)["metadata"]["campoNovo"] == 42
