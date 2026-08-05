"""Helpers da API de contas.

O teste do `linha()` existe por um motivo específico: o bug que ele previne
quebrava a resposta DEPOIS de a escrita já ter acontecido. O cliente via um
erro, tentava de novo, e recebia "já existe" — o pior tipo de falha, porque
o efeito colateral acontece e a confirmação não.
"""

from olimpo.api.routers.accounts import PRESENCE_SQL, SORTS, _slug, linha


class RangeFalso:
    """Imita o `int4range` como o driver o devolve — a classe se chama
    `Range` e o serializador do FastAPI não a conhece."""

    __name__ = "Range"

    def __init__(self, lower: int, upper: int) -> None:
        self.lower, self.upper = lower, upper


RangeFalso.__qualname__ = "Range"
Range = type("Range", (), {"__init__": RangeFalso.__init__})


class TestSerializacao:
    def test_converte_range_em_algo_serializavel(self):
        row = {"handle": "@x", "active_hours": Range(8, 23)}
        out = linha(row)
        assert out["active_hours"] == {"inicio": 8, "fim": 23}

    def test_preserva_o_resto_intacto(self):
        row = {"handle": "@x", "followers": 10, "labels": ["a"], "profile": {"k": "v"}}
        assert linha(row) == row

    def test_lida_com_range_nulo(self):
        assert linha({"active_hours": None})["active_hours"] is None


class TestSlug:
    def test_gera_identificador_estavel(self):
        assert _slug("Luna Prado") == "luna-prado"
        assert _slug("@luna.prado") == "luna-prado"
        assert _slug("  Chris No Beat  ") == "chris-no-beat"

    def test_nao_deixa_hifen_nas_pontas(self):
        assert _slug("!!! teste !!!") == "teste"


class TestPresenca:
    """Presença é o que a realidade diz; `status` é o que queremos. Os quatro
    estados precisam existir no SQL, senão a divergência entre intenção e
    realidade fica invisível — que é justamente o que o operador precisa ver."""

    def test_cobre_os_quatro_estados(self):
        for estado in ("unbound", "online", "stale", "missing"):
            assert f"'{estado}'" in PRESENCE_SQL

    def test_sem_placement_e_unbound_e_nao_missing(self):
        """Conta que nunca esteve num device não é conta sumida — a diferença
        importa: uma é cadastro novo, a outra é possível incidente."""
        i_unbound = PRESENCE_SQL.index("'unbound'")
        i_missing = PRESENCE_SQL.index("'missing'")
        assert i_unbound < i_missing, "unbound precisa ser avaliado antes"


class TestOrdenacao:
    def test_ordenacoes_nao_aceitam_entrada_livre(self):
        """`sort` vai direto para o ORDER BY: só valores de um mapa fechado
        podem chegar lá."""
        assert "handle" in SORTS
        assert all(";" not in v and "--" not in v for v in SORTS.values())
