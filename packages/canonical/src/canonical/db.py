"""Engine creation and the shared table metadata (SQLAlchemy Core, no ORM).

Every app table lives in one schema. In the code and SQL it is always written `automn` (SCHEMA): the Table objects
carry it, and hand-written SQL names it explicitly (`FROM automn.c_account`). Nothing relies on the database user's
default schema. SQLite (tests, local development) gets the schema as an attached database of that name.

The schema is configuration (CONVERGENCE-PLAN.md section 3; owner, 2026-11-27): `make_engine(schema=...)` or
BSC_SCHEMA, default `automn` (rarfincare). `automn` stays the token in all SQL text, including the frozen migrations;
for another name the engine maps it: schema_translate_map for the Table objects, and every statement's text has the
word `automn` replaced. For `automn` nothing is added, so rarfincare's statements are unchanged.
"""

from __future__ import annotations

import os
import re
from collections.abc import Callable, Iterable
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import URL, Engine, MetaData, create_engine, event, make_url

SCHEMA = "automn"  # the token in code and SQL; the database's schema name is configuration (schema_name)
metadata = MetaData(schema=SCHEMA)
_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,127}")
_TOKEN = re.compile(rf"\b{SCHEMA}\b", re.IGNORECASE)


def configured_schema(schema: str | None = None) -> str:
    """The schema name to use: the argument, else BSC_SCHEMA, else `automn`. A plain identifier only."""
    name = schema or os.environ.get("BSC_SCHEMA") or SCHEMA
    if not _IDENTIFIER.fullmatch(name):
        raise ValueError(f"schema name {name!r}: letters, digits and _ only, not starting with a digit")
    return name


def schema_name(bind: Any) -> str:
    """The real schema name behind `automn` for an engine or connection (for inspector calls and bound values)."""
    mapping = bind.get_execution_options().get("schema_translate_map") or {}
    return str(mapping.get(SCHEMA, SCHEMA))


EngineHook = Callable[[Engine], None]


def make_engine(
    database_url: str | URL, *, hooks: Iterable[EngineHook] = (), schema: str | None = None, **kwargs: Any
) -> Engine:
    """SQLAlchemy engine for the app schema. SQLite (tests, local runs) gets the schema attached and the T-SQL
    shims; SQL Server gets bulk inserts. `hooks` add sign-in from the shell (e.g. shell_azure's Entra token for
    Azure SQL, an RDS IAM token on AWS): the core never imports a cloud SDK. `schema`: see the module docstring."""
    name = configured_schema(schema)
    url = make_url(database_url)
    if url.get_backend_name() == "mssql":
        kwargs.setdefault("fast_executemany", True)  # bulk inserts (staging, canonical rows)
    if name != SCHEMA:
        options = dict(kwargs.pop("execution_options", None) or {})
        kwargs["execution_options"] = {**options, "schema_translate_map": {SCHEMA: name}}
    # pool_pre_ping avoids "server has gone away" errors on idle worker connections
    engine = create_engine(url, pool_pre_ping=True, future=True, **kwargs)
    if name != SCHEMA:
        _map_schema(engine, name)
    if engine.dialect.name == "sqlite":
        _attach_schema(engine, name)
    for hook in hooks:
        hook(engine)
    return engine


def map_schema_sql(bind: Any, sql: str) -> str:
    """The statement text with the token `automn` replaced by the bind's schema. For code that runs SQL on the raw
    driver cursor (migrate.run_batch), which the engine's statement hook does not see."""
    name = schema_name(bind)
    return sql if name == SCHEMA else _TOKEN.sub(name, sql)


def _map_schema(engine: Engine, name: str) -> None:
    """Replace the word `automn` with the configured name in every statement's text (hand-written SQL, rule SQL,
    migrations, views). Table objects go through schema_translate_map; this covers the rest."""

    @event.listens_for(engine, "before_cursor_execute", retval=True)
    def _rewrite(conn, cursor, statement, parameters, context, executemany):
        return _TOKEN.sub(name, statement), parameters


def _attach_schema(engine: Engine, name: str = SCHEMA) -> None:
    """SQLite stands in for Azure SQL (tests, local development). It has no schemas: attach a database named SCHEMA
    (in memory for an in-memory database, else a file next to the main one), so `automn.<table>` works the same.
    It also gets the T-SQL functions the SQL uses (LEN, REGEXP_LIKE, CONCAT_WS), so the SQL is the same text."""
    path = engine.url.database
    target = ":memory:" if not path or path == ":memory:" else f"{path}.{name}"

    @event.listens_for(engine, "connect")
    def _attach(dbapi_conn, _):
        dbapi_conn.execute(f"ATTACH DATABASE '{target}' AS {name}")
        dbapi_conn.execute("PRAGMA foreign_keys = ON")  # Azure SQL enforces them; SQLite only if asked
        dbapi_conn.create_function("LEN", 1, _len, deterministic=True)
        dbapi_conn.create_function("REGEXP_LIKE", 2, _regexp_like, deterministic=True)
        dbapi_conn.create_function("CONCAT_WS", -1, _concat_ws, deterministic=True)


def _len(value):
    """T-SQL LEN: characters, trailing spaces not counted."""
    return None if value is None else len(str(value).rstrip(" "))


def _regexp_like(value, pattern):
    return None if value is None or pattern is None else int(re.search(pattern, str(value)) is not None)


def _concat_ws(sep, *args):
    return sep.join(str(a) for a in args if a is not None)


def utcnow() -> datetime:
    """Naive UTC timestamp; all DATETIME columns store UTC."""
    return datetime.now(UTC).replace(tzinfo=None)
