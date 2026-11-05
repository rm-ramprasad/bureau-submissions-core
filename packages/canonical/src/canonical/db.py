"""Engine creation and the shared table metadata (SQLAlchemy Core, no ORM).

Every app table lives in the schema SCHEMA (`automn`): the Table objects carry it, so SQLAlchemy writes `automn.<table>`,
and hand-written SQL names it explicitly (`FROM automn.c_account`). Nothing relies on the database user's default
schema. SQLite (tests, local development) gets the schema as an attached database of that name.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import URL, Engine, MetaData, create_engine, event, make_url

SCHEMA = "automn"
metadata = MetaData(schema=SCHEMA)


EngineHook = Callable[[Engine], None]


def make_engine(database_url: str | URL, *, hooks: Iterable[EngineHook] = (), **kwargs: Any) -> Engine:
    """SQLAlchemy engine for the app schema. SQLite (tests, local runs) gets the schema attached and the T-SQL
    shims; SQL Server gets bulk inserts. `hooks` add sign-in from the shell (e.g. shell_azure's Entra token for
    Azure SQL, an RDS IAM token on AWS): the core never imports a cloud SDK."""
    url = make_url(database_url)
    if url.get_backend_name() == "mssql":
        kwargs.setdefault("fast_executemany", True)  # bulk inserts (staging, canonical rows)
    # pool_pre_ping avoids "server has gone away" errors on idle worker connections
    engine = create_engine(url, pool_pre_ping=True, future=True, **kwargs)
    if engine.dialect.name == "sqlite":
        _attach_schema(engine)
    for hook in hooks:
        hook(engine)
    return engine


def _attach_schema(engine: Engine) -> None:
    """SQLite stands in for Azure SQL (tests, local development). It has no schemas: attach a database named SCHEMA
    (in memory for an in-memory database, else a file next to the main one), so `automn.<table>` works the same.
    It also gets the T-SQL functions the SQL uses (LEN, REGEXP_LIKE, CONCAT_WS), so the SQL is the same text."""
    path = engine.url.database
    target = ":memory:" if not path or path == ":memory:" else f"{path}.{SCHEMA}"

    @event.listens_for(engine, "connect")
    def _attach(dbapi_conn, _):
        dbapi_conn.execute(f"ATTACH DATABASE '{target}' AS {SCHEMA}")
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
