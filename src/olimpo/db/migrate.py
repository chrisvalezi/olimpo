"""Runner de migrações em SQL puro.

Aplica os arquivos de db/migrations/ em ordem, uma vez cada, registrando o
checksum em schema_migrations. Migração já aplicada nunca é reeditada — se
o checksum divergir, o runner recusa e exige intervenção humana.

Ver ADR-0005 (por que SQL puro em vez de Alembic).

    python -m olimpo.db.migrate up      aplica pendentes
    python -m olimpo.db.migrate status  lista o estado
"""

import hashlib
import sys
from pathlib import Path

import psycopg

from olimpo.core.config import settings

MIGRATIONS_DIR = Path(__file__).resolve().parents[3] / "db" / "migrations"

BOOTSTRAP = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version    text PRIMARY KEY,
    applied_at timestamptz NOT NULL DEFAULT now(),
    checksum   text NOT NULL
);
"""


def _checksum(sql: str) -> str:
    return hashlib.sha256(sql.encode()).hexdigest()[:16]


def _discover() -> list[tuple[str, Path]]:
    files = sorted(MIGRATIONS_DIR.glob("*.sql"))
    return [(f.stem, f) for f in files]


def _applied(conn: psycopg.Connection) -> dict[str, str]:
    with conn.cursor() as cur:
        cur.execute("SELECT version, checksum FROM schema_migrations")
        return dict(cur.fetchall())


def up() -> int:
    with psycopg.connect(settings.sync_database_url, autocommit=False) as conn:
        with conn.cursor() as cur:
            cur.execute(BOOTSTRAP)
        conn.commit()

        applied = _applied(conn)
        pending = []

        for version, path in _discover():
            sql = path.read_text()
            checksum = _checksum(sql)
            if version in applied:
                if applied[version] != checksum:
                    print(
                        f"✗ {version}: checksum divergente. "
                        "Uma migração aplicada foi editada — isso não é suportado. "
                        "Crie uma nova migração em vez de alterar esta."
                    )
                    return 1
                continue
            pending.append((version, sql, checksum))

        if not pending:
            print("✓ Nada pendente.")
            return 0

        # Cada migração em sua própria transação: uma falha não desfaz as anteriores.
        for version, sql, checksum in pending:
            print(f"→ aplicando {version} ...", end=" ", flush=True)
            try:
                with conn.cursor() as cur:
                    cur.execute(sql)
                    cur.execute(
                        "INSERT INTO schema_migrations (version, checksum) VALUES (%s, %s)",
                        (version, checksum),
                    )
                conn.commit()
                print("ok")
            except Exception as exc:
                conn.rollback()
                print(f"FALHOU\n{exc}")
                return 1

        print(f"✓ {len(pending)} migração(ões) aplicada(s).")
        return 0


def status() -> int:
    with psycopg.connect(settings.sync_database_url) as conn:
        with conn.cursor() as cur:
            cur.execute(BOOTSTRAP)
        conn.commit()
        applied = _applied(conn)

    for version, _ in _discover():
        mark = "✓" if version in applied else " "
        print(f" [{mark}] {version}")
    return 0


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "up"
    sys.exit({"up": up, "status": status}.get(cmd, up)())
