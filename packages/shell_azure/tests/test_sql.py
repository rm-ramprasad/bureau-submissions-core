"""Azure SQL sign-in (shell_azure.sql)."""

import struct
from types import SimpleNamespace

from sqlalchemy import make_url
from sqlalchemy.dialects.mssql.pyodbc import MSDialect_pyodbc

from canonical.db import make_engine
from shell_azure.sql import _SQL_COPT_SS_ACCESS_TOKEN, add_access_token, azure_sql_url, entra_token_hook


def connect_args(url):
    cargs, cparams = MSDialect_pyodbc().create_connect_args(make_url(url))
    return list(cargs), dict(cparams)


def test_managed_identity_connection_has_a_token_and_no_integrated_security() -> None:
    cargs, cparams = connect_args(azure_sql_url("srv.database.windows.net", "db"))
    assert "Trusted_Connection=Yes" in cargs[0]  # what SQLAlchemy does for a URL without a user name
    add_access_token(cargs, cparams, "eyJ0b2tlbg")
    assert "trusted_connection" not in cargs[0].lower()  # the driver rejects a token with it (FA005)
    assert "UID=" not in cargs[0] and "PWD=" not in cargs[0]
    assert "Server=srv.database.windows.net,1433" in cargs[0] and "Encrypt=yes" in cargs[0]
    packed = cparams["attrs_before"][_SQL_COPT_SS_ACCESS_TOKEN]
    (length,) = struct.unpack("<I", packed[:4])
    assert packed[4:].decode("utf-16-le") == "eyJ0b2tlbg" and length == len(packed) - 4


def _engine(dialect: str, username: str | None) -> SimpleNamespace:
    return SimpleNamespace(dialect=SimpleNamespace(name=dialect), url=SimpleNamespace(username=username))


def test_hook_applies_only_to_sql_server_without_a_user(monkeypatch) -> None:
    registered = []
    monkeypatch.setattr("shell_azure.sql.event.listens_for", lambda target, name: registered.append)
    hook = entra_token_hook(lambda: "eyJ0b2tlbg")
    hook(_engine("sqlite", None))
    hook(_engine("mssql", "user1"))
    assert registered == []
    hook(_engine("mssql", None))
    (listener,) = registered
    cargs, cparams = connect_args(azure_sql_url("srv", "db"))
    listener(None, None, cargs, cparams)
    assert _SQL_COPT_SS_ACCESS_TOKEN in cparams["attrs_before"]


def test_make_engine_runs_hooks() -> None:
    seen = []
    engine = make_engine("sqlite://", hooks=[seen.append])
    assert seen == [engine]
