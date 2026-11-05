"""Azure SQL sign-in: an Entra ID access token on every new connection (the Function App's managed identity, or
`az login` locally). Passed to canonical.db.make_engine as a hook, so the core never imports a cloud SDK."""

from __future__ import annotations

import re
import struct
from collections.abc import Callable
from typing import Any

from sqlalchemy import URL, Engine, event

ODBC_DRIVER = "ODBC Driver 18 for SQL Server"
_SQL_COPT_SS_ACCESS_TOKEN = 1256  # pyodbc connection attribute: an Entra access token
TOKEN_SCOPE = "https://database.windows.net/.default"  # noqa: S105  (a scope, not a secret)


def azure_sql_url(server: str, database: str) -> URL:
    """mssql+pyodbc URL for Azure SQL (encrypted, certificate checked), Entra token authentication."""
    return URL.create(
        "mssql+pyodbc",
        host=server,
        port=1433,
        database=database,
        query={"driver": ODBC_DRIVER, "Encrypt": "yes", "TrustServerCertificate": "no"},
    )


def add_access_token(cargs: list[Any], cparams: dict[str, Any], token: str) -> None:
    """Attach an Entra access token to a pyodbc connect call. SQLAlchemy adds Trusted_Connection=Yes to the ODBC
    connection string when the URL has no user name, and the driver refuses a token combined with it (FA005)."""
    cargs[0] = re.sub(r";?\s*Trusted_Connection\s*=\s*Yes\s*", "", cargs[0], flags=re.IGNORECASE)
    raw = token.encode("utf-16-le")
    cparams.setdefault("attrs_before", {})[_SQL_COPT_SS_ACCESS_TOKEN] = struct.pack(f"<I{len(raw)}s", len(raw), raw)


def entra_token_hook(get_token: Callable[[], str]) -> Callable[[Engine], None]:
    """An engine hook: each new SQL Server connection without a user name gets a fresh token from `get_token`."""

    def hook(engine: Engine) -> None:
        if engine.dialect.name != "mssql" or engine.url.username:
            return

        @event.listens_for(engine, "do_connect")
        def _token(dialect: Any, conn_rec: Any, cargs: list[Any], cparams: dict[str, Any]) -> None:
            add_access_token(cargs, cparams, get_token())

    return hook
