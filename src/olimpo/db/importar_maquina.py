"""Importa a máquina de influencers para dentro do Olimpo.

Lê o snapshot SQLite preservado, copia a mídia para o armazenamento do
Olimpo e reconstrói tudo em Postgres: personas, imagens de identidade,
guarda-roupa, marcas, mídia gerada e rascunhos de vídeo.

    docker compose exec api python -m olimpo.db.importar_maquina

Idempotente: reexecutar não duplica. Isso é garantido por índices únicos
(`legacy_id` em marcas e figurinos, hash de conteúdo em mídia, persona+prompt
em rascunhos), não por o código lembrar de checar — a primeira versão
prometia idempotência com `ON CONFLICT DO NOTHING` sem restrição nenhuma
para o conflito acontecer, e duplicou tudo na segunda execução.

⚠️ A ordem das referências de identidade é contrato, não detalhe. O rosto é
sempre a primeira; a folha de personagem, a segunda; os dois close-ups vêm
depois. Trocar a ordem faz o modelo copiar a roupa da referência de rosto.
"""

from __future__ import annotations

import hashlib
import json
import mimetypes
import pathlib
import re
import shutil
import sqlite3
import sys
from datetime import UTC, datetime
from typing import Any

import psycopg

from olimpo.core.config import settings

IMPORT = pathlib.Path("/app/import")
DESTINO = pathlib.Path("/app/media/personas")

#: Campo na origem → papel no Olimpo. A ordem desta lista É a ordem em que
#: as referências entram no prompt.
IDENTIDADE: list[tuple[str, str, str]] = [
    ("main_image", "face", "face_asset_id"),
    ("character_sheet_image", "character_sheet", "character_sheet_asset_id"),
    ("close_up_image1", "close_up_face", "close_up_face_asset_id"),
    ("close_up_image2", "close_up_detail", "close_up_detail_asset_id"),
]


def slug(valor: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", valor.strip().lstrip("@").lower()).strip("-")


def quando(epoch_ms: Any) -> datetime:
    try:
        return datetime.fromtimestamp(int(epoch_ms) / 1000, tz=UTC)
    except Exception:
        return datetime.now(UTC)


def json_ou(valor: Any, padrao: Any) -> Any:
    if not valor:
        return padrao
    try:
        return json.loads(valor)
    except Exception:
        return padrao


class Resolvedor:
    """Traduz uma URL da origem para o arquivo preservado em disco."""

    def __init__(self) -> None:
        self.remoto = json.loads((IMPORT / "remote/mapa.json").read_text())
        mapa_assets = IMPORT / "assets/mapa.json"
        self.assets = json.loads(mapa_assets.read_text()) if mapa_assets.exists() else {}

    def __call__(self, url: str | None) -> pathlib.Path | None:
        if not url:
            return None
        if url.startswith("http"):
            nome = self.remoto.get(url)
            caminho = IMPORT / "remote" / nome if nome else None
        elif url.startswith("/media/"):
            caminho = IMPORT / "media" / url.split("/media/", 1)[1]
        elif url.startswith("/blobs/"):
            caminho = IMPORT / "blobs" / url.split("/blobs/", 1)[1]
        else:
            rel = self.assets.get(url)
            caminho = IMPORT / "assets" / rel if rel else None
        return caminho if caminho and caminho.exists() else None


class Importador:
    def __init__(self, conn: psycopg.Connection) -> None:
        self.conn = conn
        self.resolve = Resolvedor()
        self.origem = sqlite3.connect(IMPORT / "db/snapshot.db")
        self.origem.row_factory = sqlite3.Row
        DESTINO.mkdir(parents=True, exist_ok=True)
        self.contagem: dict[str, int] = {}

    def conta(self, chave: str, n: int = 1) -> None:
        self.contagem[chave] = self.contagem.get(chave, 0) + n

    # ------------------------------------------------------------------ mídia
    def guarda_asset(
        self,
        url: str | None,
        *,
        persona_id: str | None,
        role: str,
        kind: str = "image",
        legacy_id: str | None = None,
        **extra: Any,
    ) -> str | None:
        """Copia o arquivo para o armazenamento e devolve o id do asset.

        Dedupe por hash: a mesma imagem usada por duas personas ocupa disco
        uma vez só, e reexecutar a importação não duplica nada.
        """
        arquivo = self.resolve(url)
        if not arquivo:
            if url:
                self.conta("mídia sem arquivo")
            return None

        dados = arquivo.read_bytes()
        digest = hashlib.sha256(dados).hexdigest()

        with self.conn.cursor() as cur:
            cur.execute("SELECT id FROM media_assets WHERE content_hash = %s", (digest,))
            if achado := cur.fetchone():
                return str(achado[0])

            nome = f"{digest[:24]}{arquivo.suffix or '.bin'}"
            alvo = DESTINO / nome
            if not alvo.exists():
                shutil.copy2(arquivo, alvo)

            cur.execute(
                """
                INSERT INTO media_assets
                    (persona_id, kind, role, storage_path, source_url, content_hash,
                     mime, bytes, prompt, provider, model, aspect_ratio, params,
                     label, source, legacy_id, created_at)
                VALUES (%s, %s::media_kind, %s::media_role, %s, %s, %s, %s, %s,
                        %s, %s, %s, %s, %s::jsonb, %s, %s, %s, %s)
                RETURNING id
                """,
                (
                    persona_id, kind, role, f"personas/{nome}", url, digest,
                    mimetypes.guess_type(arquivo.name)[0], len(dados),
                    extra.get("prompt"), extra.get("provider"), extra.get("model"),
                    extra.get("aspect_ratio"),
                    json.dumps(extra.get("params") or {}),
                    extra.get("label"), extra.get("source"), legacy_id,
                    extra.get("created_at") or datetime.now(UTC),
                ),
            )
            self.conta("assets")
            return str(cur.fetchone()[0])

    # --------------------------------------------------------------- personas
    def personas(self) -> dict[str, str]:
        mapa: dict[str, str] = {}
        for linha in self.origem.execute("SELECT * FROM influencers ORDER BY sort_order"):
            legacy = linha["id"]
            nome = linha["name"]

            with self.conn.cursor() as cur:
                cur.execute("SELECT id FROM personas WHERE legacy_id = %s", (legacy,))
                achado = cur.fetchone()
                if achado:
                    persona_id = str(achado[0])
                else:
                    # A persona pode já existir pelo nome — a Luna do seed, por
                    # exemplo. Casar em vez de criar uma segunda.
                    base = slug(nome)
                    cur.execute("SELECT id FROM personas WHERE slug = %s", (base,))
                    if existente := cur.fetchone():
                        persona_id = str(existente[0])
                        cur.execute(
                            "UPDATE personas SET legacy_id = %s WHERE id = %s",
                            (legacy, persona_id),
                        )
                    else:
                        cur.execute(
                            """
                            INSERT INTO personas (name, slug, persona_card, legacy_id,
                                                  notes, created_at)
                            VALUES (%s, %s, '{}'::jsonb, %s, %s, %s) RETURNING id
                            """,
                            (nome, base, legacy,
                             "importada da máquina de influencers",
                             quando(linha["created_at"])),
                        )
                        persona_id = str(cur.fetchone()[0])
                        self.conta("personas criadas")

                idade = None
                if linha["age"] and str(linha["age"]).strip().isdigit():
                    idade = int(str(linha["age"]).strip())

                cur.execute(
                    """
                    UPDATE personas SET
                        gender = COALESCE(%s, gender),
                        apparent_age = COALESCE(%s, apparent_age),
                        physical_desc = COALESCE(%s, physical_desc),
                        backstory = COALESCE(%s, backstory),
                        personality_energy = COALESCE(%s, personality_energy),
                        niches = %s, clothing_style = COALESCE(%s, clothing_style),
                        palette = %s, content_pillars = %s,
                        home_slots = %s::jsonb
                     WHERE id = %s
                    """,
                    (
                        linha["gender"], idade, linha["physical_desc"],
                        linha["backstory"], linha["intro_extrovert"],
                        [n.strip() for n in (linha["niche"] or "").split(",") if n.strip()],
                        linha["clothing_style"],
                        json_ou(linha["palette"], []),
                        json_ou(linha["content_pillars"], []),
                        json.dumps(json_ou(linha["home_images"], [])),
                        persona_id,
                    ),
                )

            # As quatro âncoras, na ordem que o prompt espera.
            for campo, role, coluna in IDENTIDADE:
                asset = self.guarda_asset(
                    linha[campo], persona_id=persona_id, role=role,
                    prompt=linha["prompt"] if campo == "main_image" else None,
                    source="profile", created_at=quando(linha["created_at"]),
                )
                if asset:
                    with self.conn.cursor() as cur:
                        cur.execute(
                            f"UPDATE personas SET {coluna} = %s WHERE id = %s",
                            (asset, persona_id),
                        )

            self.rascunhos(linha, persona_id)
            mapa[legacy] = persona_id
        return mapa

    def rascunhos(self, linha: sqlite3.Row, persona_id: str) -> None:
        for d in json_ou(linha["settings"], {}).get("videoDrafts", []) or []:
            if not d.get("prompt"):
                continue
            with self.conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO video_drafts
                        (persona_id, dialogue, prompt, duration_seconds, aspect_ratio,
                         shot_mode, camera, vibe, voice, environment, product_worn,
                         notes, params, created_at)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s)
                    ON CONFLICT (persona_id, md5(prompt)) DO NOTHING
                    """,
                    (
                        persona_id, d.get("dialogue"), d["prompt"], d.get("duration"),
                        d.get("aspect"), d.get("shotMode"), d.get("camera"),
                        d.get("vibe"), d.get("voiceCustom") or d.get("voicePreset"),
                        d.get("environment"), bool(d.get("productWorn")),
                        d.get("additionalNotes"), json.dumps(d),
                        quando(d.get("ts")),
                    ),
                )
                self.conta("rascunhos de vídeo")

    # ----------------------------------------------------------------- resto
    def midia(self, mapa: dict[str, str]) -> None:
        for linha in self.origem.execute("SELECT * FROM media ORDER BY created_at"):
            persona_id = mapa.get(linha["influencer_id"] or "")
            self.guarda_asset(
                linha["url"], persona_id=persona_id, role="generated",
                kind=linha["type"] if linha["type"] in ("image", "video") else "image",
                legacy_id=linha["id"], prompt=linha["prompt"],
                provider=linha["provider"], model=linha["model"],
                aspect_ratio=linha["aspect_ratio"],
                params=json_ou(linha["settings"], {}),
                label=linha["label"], source=linha["source"],
                created_at=quando(linha["created_at"]),
            )

    def guarda_roupa(self, mapa: dict[str, str]) -> None:
        for linha in self.origem.execute("SELECT * FROM wardrobe_slots ORDER BY sort_order"):
            persona_id = mapa.get(linha["influencer_id"] or "")
            if not persona_id:
                continue
            asset = self.guarda_asset(
                linha["image"], persona_id=persona_id, role="wardrobe",
                source="wardrobe", created_at=quando(linha["created_at"]),
            )
            with self.conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO wardrobe_items
                        (persona_id, name, asset_id, sort_order, legacy_id, created_at)
                    VALUES (%s,%s,%s,%s,%s,%s)
                    ON CONFLICT (legacy_id) WHERE legacy_id IS NOT NULL
                    DO UPDATE SET asset_id = COALESCE(EXCLUDED.asset_id,
                                                      wardrobe_items.asset_id)
                    """,
                    (persona_id, linha["name"] or "sem nome", asset,
                     linha["sort_order"] or 0, linha["id"],
                     quando(linha["created_at"])),
                )
                self.conta("figurinos")

    def marcas(self, mapa: dict[str, str]) -> None:
        for linha in self.origem.execute("SELECT * FROM brand_deals ORDER BY sort_order"):
            persona_id = mapa.get(linha["influencer_id"] or "")
            fotos = [
                a for a in (
                    self.guarda_asset(u, persona_id=persona_id, role="brand_product",
                                      source="brand_deal",
                                      created_at=quando(linha["created_at"]))
                    for u in json_ou(linha["images"], [])
                ) if a
            ]
            folha = self.guarda_asset(
                linha["character_sheet"], persona_id=persona_id,
                role="brand_product", source="brand_deal",
                created_at=quando(linha["created_at"]),
            )
            with self.conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO brands (persona_id, name, category, asset_ids,
                                        product_sheet_asset_id, legacy_id, created_at)
                    VALUES (%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT (legacy_id) WHERE legacy_id IS NOT NULL
                    DO UPDATE SET product_sheet_asset_id =
                        COALESCE(EXCLUDED.product_sheet_asset_id,
                                 brands.product_sheet_asset_id)
                    """,
                    (persona_id, linha["brand"] or "sem nome", linha["category"],
                     fotos, folha, linha["id"], quando(linha["created_at"])),
                )
                self.conta("marcas")

    def vincula_contas(self) -> None:
        """Liga persona a login quando o identificador é o mesmo.

        É o ponto onde os dois mundos se encontram: a máquina conhecia a
        personagem e a estética; o orchestrator descobriu o login rodando num
        device. Ninguém sabia que eram a mesma coisa.

        Só vincula conta que ainda não tem persona — nunca sobrescreve uma
        decisão humana.
        """
        with self.conn.cursor() as cur:
            cur.execute("""
                UPDATE accounts a SET persona_id = p.id
                  FROM personas p
                 WHERE a.persona_id IS NULL
                   AND p.slug = regexp_replace(
                         lower(trim(leading '@' from a.handle)), '[^a-z0-9]+', '-', 'g')
                RETURNING a.handle, p.name
            """)
            for handle, nome in cur.fetchall():
                print(f"   vinculado: {handle} → {nome}")
                self.conta("contas vinculadas")

    def rodar(self) -> None:
        print("→ personas e imagens de identidade…")
        mapa = self.personas()
        print("→ guarda-roupa…")
        self.guarda_roupa(mapa)
        print("→ marcas…")
        self.marcas(mapa)
        print("→ mídia gerada…")
        self.midia(mapa)
        print("→ vinculando personas aos logins…")
        self.vincula_contas()
        self.conn.commit()


def main() -> int:
    if not (IMPORT / "db/snapshot.db").exists():
        print("✗ snapshot não encontrado — rode scripts/preservar_midia.py antes")
        return 1

    with psycopg.connect(settings.sync_database_url) as conn:
        imp = Importador(conn)
        imp.rodar()

    print("\n✓ importação concluída")
    for chave, n in sorted(imp.contagem.items()):
        print(f"   {n:5d}  {chave}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
