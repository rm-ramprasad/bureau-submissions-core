"""Schema migrations and source views, as plain SQL files shipped in this package (`canonical/migrations/`).

- `migrations/azure/NNN_*.sql`: T-SQL migrations for Azure SQL, applied in order, once each, recorded in
  `automn.schema_migration`. The baseline (001) is run once by an administrator and records itself, so the runner only
  applies later files. Each file runs in one transaction (no BEGIN/COMMIT in it). Statements are split on ';' at line
  end: keep semicolons out of string literals, and name every table with its schema (`automn.<table>`).
- `migrations/<dialect>/views/*.sql`: the source views (`automn.vw_<type>_<entity>`), safe to run again. The T-SQL
  files are the source; the Postgres and MySQL copies are generated from them (`python -m canonical.build_views`).

The files on rarfincare were applied from the reference app: they are copied here byte for byte, so the history in
`automn.schema_migration` stays valid (Option A: reuse the existing schema).
"""

from __future__ import annotations

import logging
import re
from importlib.resources import files
from pathlib import Path

from sqlalchemy import Column, Connection, DateTime, Engine, MetaData, String, Table, insert, select, text

from canonical.db import SCHEMA, utcnow

log = logging.getLogger(__name__)

MIGRATIONS_ROOT = Path(str(files("canonical") / "migrations"))
AZURE_MIGRATIONS = MIGRATIONS_ROOT / "azure"
VIEW_DIALECTS = ("azure", "postgres", "mysql")

schema_migration = Table(  # not on the app metadata: the baseline file creates it with the rest
    "schema_migration",
    MetaData(schema=SCHEMA),
    Column("version", String(100), primary_key=True),
    Column("applied_at", DateTime, nullable=False),
)

_GO = re.compile(r"^\s*GO\s*$", re.I | re.M)


def _drop_comment_lines(sql: str) -> list[str]:
    return [ln for ln in sql.splitlines() if not ln.strip().startswith("--")]


def split_statements(sql: str) -> list[str]:
    """Statements of a migration file: split on ';' at line end; whole-line comments dropped."""
    statements, current = [], []
    for line in _drop_comment_lines(sql):
        current.append(line)
        if line.rstrip().endswith(";"):
            statements.append("\n".join(current).strip().rstrip(";"))
            current = []
    tail = "\n".join(current).strip()
    if tail:
        statements.append(tail)
    return [s for s in statements if s]


def split_batches(sql: str) -> list[str]:
    """T-SQL batches of a view file: split on lines holding only GO; batches with nothing but comments dropped."""
    batches = [b.strip() for b in _GO.split(sql)]
    return [b for b in batches if "\n".join(_drop_comment_lines(b)).strip()]


def pending_migrations(engine: Engine, migrations_dir: Path = AZURE_MIGRATIONS) -> list[Path]:
    schema_migration.create(engine, checkfirst=True)
    with engine.connect() as conn:
        applied = set(conn.execute(select(schema_migration.c.version)).scalars())
    return [p for p in sorted(migrations_dir.glob("*.sql")) if p.stem not in applied]


def apply_migrations(engine: Engine, migrations_dir: Path = AZURE_MIGRATIONS) -> list[str]:
    """Apply the pending migration files; returns their versions."""
    applied: list[str] = []
    for path in pending_migrations(engine, migrations_dir):
        log.info("Applying migration %s", path.name)
        try:
            with engine.begin() as conn:  # the file and its version row commit together
                for stmt in split_statements(path.read_text(encoding="utf-8")):
                    conn.execute(text(stmt))
                conn.execute(insert(schema_migration).values(version=path.stem, applied_at=utcnow()))
        except Exception as exc:
            raise MigrationFailed(path.stem, applied, exc) from exc
        applied.append(path.stem)
    return applied


class MigrationFailed(Exception):
    """A migration file failed (its transaction was rolled back); `version` names it."""

    def __init__(self, version: str, applied: list[str], cause: Exception) -> None:
        super().__init__(f"{version}: {cause}")
        self.version, self.applied, self.cause = version, applied, cause


def mark_applied(engine: Engine, version: str, migrations_dir: Path = AZURE_MIGRATIONS) -> bool:
    """Record a migration as applied without running it: for one that was run by hand (e.g. in SSMS), whose change
    is already in the database. Only a version that exists as a file and is still pending can be marked. True if
    recorded."""
    pending = {p.stem for p in pending_migrations(engine, migrations_dir)}
    known = {p.stem for p in migrations_dir.glob("*.sql")}
    if version not in known:
        raise ValueError(f"No migration {version!r} (known: {', '.join(sorted(known))})")
    if version not in pending:
        return False
    with engine.begin() as conn:
        conn.execute(insert(schema_migration).values(version=version, applied_at=utcnow()))
    return True


def view_files(dialect: str) -> list[Path]:
    """The view files of a dialect, in the order they must run (00_functions, 01_reference, then the rest)."""
    if dialect not in VIEW_DIALECTS:
        raise ValueError(f"unknown dialect {dialect!r}: one of {', '.join(VIEW_DIALECTS)}")
    return sorted((MIGRATIONS_ROOT / dialect / "views").glob("*.sql"))


def apply_views(engine: Engine) -> list[str]:
    """Create or replace the source views (and their helper functions) in the engine's database.

    SQL Server: each GO batch runs on its own. Postgres: each file runs whole (its functions hold ';' inside $$)."""
    dialect = {"mssql": "azure", "postgresql": "postgres", "mysql": "mysql"}.get(engine.dialect.name)
    if dialect is None:
        raise ValueError(f"no source views for {engine.dialect.name}")
    if dialect == "mysql":
        raise NotImplementedError("MySQL views are generated but not applied yet (no MySQL target)")
    done = []
    for path in view_files(dialect):
        sql = path.read_text(encoding="utf-8")
        with engine.begin() as conn:
            for batch in split_batches(sql) if dialect == "azure" else [sql]:
                run_batch(conn, batch)
        done.append(path.name)
    return done


def run_batch(conn: Connection, sql: str) -> None:
    """Run one batch of several statements. SQL Server: with NOCOUNT on and every result read, because the ODBC
    driver can cancel the rest of a batch whose 'rows affected' results are left unread (labels.sql has hundreds of
    UPDATEs; found by the Database CI job, 2026-11-05)."""
    if conn.dialect.name != "mssql":
        conn.exec_driver_sql(sql)
        return
    cursor = conn.connection.driver_connection.cursor()  # type: ignore[union-attr]
    try:
        cursor.execute("SET NOCOUNT ON")  # its own statement: CREATE VIEW / FUNCTION must start their batch
        cursor.execute(sql)
        while cursor.nextset():
            pass
    finally:
        # back to the default: the pooled connection is reused, and the app reads UPDATE row counts
        cursor.execute("SET NOCOUNT OFF")
        cursor.close()
