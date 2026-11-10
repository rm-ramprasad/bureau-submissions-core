"""The Azure shell: settings from the environment, the SQL login file, queue dispatch, admin jobs, the Function App's
functions and handlers (with fakes for the Azure clients and SQLite for the database)."""

import json
from datetime import date
from pathlib import Path

import pytest

from app.enums import RunMode
from app.requests import RequestRepository
from shell_azure import admin, handlers
from shell_azure.queues import QueueJobDispatcher, make_dispatcher
from shell_azure.settings import Settings
from shell_azure.sql import url_from_env_file

ENV = {"SUBMISSIONS_DATABASE_URL": "sqlite://", "SUBMISSIONS_TENANT": "nbfc_001"}


def test_settings_from_the_environment(tmp_path):
    s = Settings.from_env(ENV | {"SUBMISSIONS_DISPATCH": "QUEUE", "SUBMISSIONS_ENTRA_ADMIN_GROUP_ID": "ABC",
                                 "SUBMISSIONS_APP_USER_FALLBACK": "0", "SUBMISSIONS_RETENTION_DAYS": "30"})  # fmt: skip
    assert (s.dispatch, s.auth, s.entra_admin_group_id, s.app_user_fallback) == ("queue", "entra", "abc", False)
    assert s.retention_days == 30 and s.source_database_url == "sqlite://" and s.require_inputs
    with pytest.raises(RuntimeError, match="SUBMISSIONS_DATABASE_URL"):
        Settings.from_env({"SUBMISSIONS_TENANT": "x"})
    with pytest.raises(RuntimeError, match="SUBMISSIONS_TENANT"):
        Settings.from_env({"SUBMISSIONS_DATABASE_URL": "sqlite://"})
    with pytest.raises(RuntimeError, match="SUBMISSIONS_STORAGE must be one of"):
        Settings.from_env(ENV | {"SUBMISSIONS_STORAGE": "ftp"})
    with pytest.raises(RuntimeError, match="at least 1"):
        Settings.from_env(ENV | {"SUBMISSIONS_RETENTION_DAYS": "0"})


def test_sign_in_settings_entra_and_generic_oidc():
    config, groups = Settings.from_env(
        ENV | {"SUBMISSIONS_ENTRA_TENANT_ID": "t-1", "SUBMISSIONS_ENTRA_CLIENT_ID": "c-1",
               "SUBMISSIONS_ENTRA_USER_GROUP_ID": "G-U"}
    ).sign_in()  # fmt: skip
    assert config is not None and config.claims == {"tid": "t-1"} and groups.user == "g-u"
    assert Settings.from_env(ENV).sign_in()[0] is None  # Entra not configured: the API answers 500
    s = Settings.from_env(
        ENV | {"SUBMISSIONS_AUTH": "OIDC", "SUBMISSIONS_OIDC_ISSUER": "https://idp.example",
               "SUBMISSIONS_OIDC_JWKS_URI": "https://idp.example/jwks", "SUBMISSIONS_OIDC_AUDIENCE": "web, api",
               "SUBMISSIONS_OIDC_GROUPS_CLAIM": "cognito:groups", "SUBMISSIONS_OIDC_ADMIN_GROUP": "Admins"}
    )  # fmt: skip
    config, groups = s.sign_in()
    assert config is not None and (config.issuers, config.audiences) == (("https://idp.example",), ("web", "api"))
    assert config.groups_claim == "cognito:groups" and groups.admin == "admins" and groups.user is None
    with pytest.raises(RuntimeError, match="SUBMISSIONS_OIDC_ISSUER"):
        Settings.from_env(ENV | {"SUBMISSIONS_AUTH": "oidc"})


def test_the_sql_login_file_is_read_and_masked(tmp_path):
    env_file = tmp_path / "test.env.sql"
    env_file.write_text(
        "# local login\nSUBMISSIONS_SQL_SERVER=srv.database.windows.net\nSUBMISSIONS_SQL_DATABASE=db\n"
        "SUBMISSIONS_SQL_USER=u1\nSUBMISSIONS_SQL_PASSWORD=p;w=d\n",
        encoding="utf-8",
    )
    url = url_from_env_file(env_file)
    assert (url.host, url.database, url.username, url.password) == ("srv.database.windows.net", "db", "u1", "p;w=d")
    assert "p;w=d" not in str(url)  # masked when printed or logged
    s = Settings.from_env({"SUBMISSIONS_SQL_ENV_FILE": str(env_file), "SUBMISSIONS_TENANT": "t"})
    assert s.database_url.username == "u1"
    env_file.write_text("SUBMISSIONS_SQL_SERVER=srv\nSUBMISSIONS_SQL_DATABASE=db\n", encoding="utf-8")
    assert url_from_env_file(env_file).username is None  # no user: Entra token sign-in


class FakeQueue:
    def __init__(self):
        self.sent = []

    def send_message(self, content, visibility_timeout=None):
        self.sent.append((content, visibility_timeout))


def test_queue_dispatcher_sends_one_message_per_row_to_its_queue(requests_repo):
    v = requests_repo.create_batch("nbfc_001", date(2026, 9, 30), RunMode.VALIDATE, ["CONSUMER"])
    g = requests_repo.create_batch(
        "nbfc_002", date(2026, 9, 30), RunMode.GENERATE, ["CONSUMER"], ["CIBIL"], snapshot_id=1
    )
    rows = requests_repo.list_batch(v) + requests_repo.list_batch(g)
    validate, generate = FakeQueue(), FakeQueue()
    dispatcher = QueueJobDispatcher(validate, generate)
    dispatcher.dispatch(rows)
    assert [json.loads(m) for m, _ in validate.sent] == [{"request_id": rows[0].request_id, "run_mode": "VALIDATE"}]
    assert [json.loads(m) for m, _ in generate.sent] == [{"request_id": rows[1].request_id, "run_mode": "GENERATE"}]
    assert validate.sent[0][1] is None  # visible at once
    dispatcher.dispatch_later(rows[:1], 120)
    assert validate.sent[1][1] == 120
    from app.ports.jobs import NoDispatch

    assert isinstance(make_dispatcher(Settings("sqlite://", "sqlite://", ".")), NoDispatch)
    with pytest.raises(RuntimeError, match="SUBMISSIONS_QUEUE_ACCOUNT_URL"):
        make_dispatcher(Settings("sqlite://", "sqlite://", ".", dispatch="queue"))


# ---------------------------------------------------------------- admin jobs
def run(engine, event, **kw):
    from app.ports.files import LocalFileStore
    from app.ports.jobs import NoDispatch

    return admin.run_job(
        event, engine, tenant="nbfc_001", dispatcher=NoDispatch, store=lambda: LocalFileStore(Path(".")), **kw
    )


def test_users_job(engine):
    out = run(engine, {"job": "users", "args": ["add", "Asha.K", "--email", "asha.k@company.in", "--name", "Asha K"]})
    assert out == {"exit_code": 0, "output": "add: asha.k done"}
    assert run(engine, {"job": "users", "args": ["email", "asha.k", "asha@company.in"]})["exit_code"] == 0
    assert run(engine, {"job": "users", "args": ["disable", "asha.k"]})["output"] == "disable: asha.k done"
    listed = run(engine, {"job": "users", "args": ["list"]})["output"]
    assert "asha.k" in listed and "disabled" in listed and "asha@company.in" in listed
    assert run(engine, {"job": "users", "args": ["enable", "nobody"]}) == {
        "exit_code": 1,
        "output": "error: No user nobody",
    }
    assert run(engine, {"job": "users", "args": ["frobnicate"]})["exit_code"] == 2
    helped = run(engine, {"job": "users", "args": ["add", "--help"]})
    assert helped["exit_code"] == 0 and "--email" in helped["output"]
    from app import audit

    assert [e.action for e in audit.recent(engine)] == ["USER_DISABLED", "USER_EMAIL_CHANGED", "USER_ADDED"]


def test_migrate_job_seeds_reference_and_profiles(engine, monkeypatch):
    monkeypatch.setattr("canonical.migrate.apply_migrations", lambda e: ["007_x"])  # T-SQL files: SQL Server only
    out = run(engine, {"job": "migrate"})
    assert out["applied"] == ["007_x"] and out["bureau_profiles_added"] == 8 and "reference" in out
    assert run(engine, {"job": "migrate"})["bureau_profiles_added"] == 0  # existing profiles are never changed


def test_migrate_reports_a_failed_migration_and_marks_a_hand_run_one(engine, monkeypatch):
    from canonical import migrate as m

    def failing(e):
        raise m.MigrationFailed("006_generate_once", [], RuntimeError("Column name 'generate_key' ... more than once"))

    monkeypatch.setattr("canonical.migrate.apply_migrations", failing)
    out = run(engine, {"job": "migrate"})
    assert out["failed"] == "006_generate_once" and "--mark-applied 006_generate_once" in out["hint"]
    marked = []
    monkeypatch.setattr("canonical.migrate.mark_applied", lambda e, v: marked.append(v) or True)
    monkeypatch.setattr("canonical.migrate.apply_migrations", lambda e: [])
    out = run(engine, {"job": "migrate", "args": ["--mark-applied", "006_generate_once"]})
    assert marked == ["006_generate_once"] and out["marked_applied"] == "006_generate_once" and out["applied"] == []
    with pytest.raises(ValueError, match="--mark-applied"):
        run(engine, {"job": "migrate", "args": ["--bogus"]})


def test_views_recovery_retention_and_unknown_jobs(engine, requests_repo):
    out = run(engine, {"job": "views", "args": ["consumer", "--as-of", "2026-09-30", "--load-id", "1"]})
    assert out["ok"] is False and "Required views missing" in out["errors"][0]
    with pytest.raises(ValueError, match="views <TYPE>"):
        run(engine, {"job": "views", "args": ["consumer"]})
    with pytest.raises(ValueError, match="master-parity <TYPE>"):
        run(engine, {"job": "master-parity", "args": ["consumer"]})
    with pytest.raises(Exception, match="Required views missing"):  # parity reads the type's views first
        run(engine, {"job": "master-parity", "args": ["consumer", "--as-of", "2026-09-30", "--load-id", "1"]})
    assert run(engine, {"job": "recovery"}) == {
        "released": [],
        "failed": [],
        "snapshots_failed": [],
        "redispatched": [],
    }
    assert run(engine, {"job": "retention", "dry_run": True})["dry_run"] is True
    with pytest.raises(ValueError, match="at least 1"):
        run(engine, {"job": "retention", "days": 0})
    with pytest.raises(ValueError, match="Unknown job"):
        run(engine, {"job": "nope"})


# ---------------------------------------------------------------- the Function App
@pytest.fixture
def azure_env(engine, monkeypatch, tmp_path):
    """The handlers on SQLite with local files and no queues (the Azure clients are not used)."""
    for k, v in (ENV | {"SUBMISSIONS_AUTH": "none", "SUBMISSIONS_OUTPUT_DIR": str(tmp_path)}).items():
        monkeypatch.setenv(k, v)
    for f in (handlers.settings, handlers.engine, handlers.source_engine, handlers.store, handlers.exports,
              handlers.dispatcher, handlers.flask_app, handlers._worker):  # fmt: skip
        f.cache_clear()
    monkeypatch.setattr(handlers, "engine", lambda: engine)
    yield
    handlers.settings.cache_clear()


def test_functions_are_the_reference_apps():
    from shell_azure.functions import app

    assert [f.get_function_name() for f in app.get_functions()] == [
        "api", "admin", "validate_worker", "generate_worker", "recovery", "retention",
    ]  # fmt: skip


def test_handlers_api_jobs_and_worker(azure_env, engine):
    client = handlers.flask_app().test_client()
    assert client.get("/api/health").get_json()["slow"] is False
    assert handlers.jobs({"job": "recovery"})["released"] == []
    requests = RequestRepository(engine)
    batch = requests.create_batch("nbfc_001", date(2026, 9, 30), RunMode.VALIDATE, ["CONSUMER"])
    (row,) = requests.list_batch(batch)
    result = handlers.worker(json.dumps({"request_id": row.request_id, "run_mode": "VALIDATE"}))
    assert result == {"ok": True, "request_id": row.request_id}
    assert requests.get(row.request_id).status.value == "FAILED"  # no source views in this database
    with pytest.raises(ValueError, match="Not a job message"):
        handlers.worker("garbage")


def test_admin_function_route(azure_env):
    import azure.functions as func

    from shell_azure.functions import admin as admin_function

    def call(job, body):
        req = func.HttpRequest("POST", f"/ops/{job}", body=json.dumps(body).encode(), route_params={"job": job})
        resp = admin_function._function._func(req) if hasattr(admin_function, "_function") else admin_function(req)
        return resp.status_code, json.loads(resp.get_body())

    assert call("recovery", {})[0] == 200
    assert call("nope", {}) == (
        400,
        {"error": "Unknown job 'nope': use migrate, users, views, master-parity, recovery, retention"},
    )
    status, body = call("master-parity", {"args": ["consumer", "--as-of", "2026-09-30", "--load-id", "1"]})
    assert status == 400 and "Required views missing" in body["error"]


def test_an_unexpected_admin_error_returns_its_reason(azure_env, monkeypatch):
    import azure.functions as func

    from shell_azure import handlers
    from shell_azure.functions import admin as admin_function

    def boom(event, **kw):
        raise RuntimeError("String or binary data would be truncated in column 'phone_number'")

    monkeypatch.setattr(handlers, "jobs", boom)
    req = func.HttpRequest("POST", "/ops/master-parity", body=b"{}", route_params={"job": "master-parity"})
    resp = admin_function._function._func(req) if hasattr(admin_function, "_function") else admin_function(req)
    assert resp.status_code == 500
    assert json.loads(resp.get_body()) == {
        "error": "RuntimeError: String or binary data would be truncated in column 'phone_number'"
    }


def test_the_local_admin_command(azure_env, capsys):
    assert admin.main([]) == 2
    assert admin.main(["retention", "--days", "0"]) == 2  # refused, not run with the default
    assert "days must be at least 1" in capsys.readouterr().err
    assert admin.main(["recovery"]) == 0 and '"released": []' in capsys.readouterr().out
    assert admin.main(["retention", "--dry-run"]) == 0
    assert admin.main(["users", "list"]) == 0


def test_migrate_reports_the_deployed_version(engine, tmp_path, monkeypatch):
    assert admin.deployed_version() == "dev"  # not a release build
    fake = tmp_path / "admin.py"
    (tmp_path / "VERSION").write_text("v1.2.3\n", encoding="utf-8")
    monkeypatch.setattr(admin, "__file__", str(fake))
    assert admin.deployed_version() == "v1.2.3"
    assert admin.migrate(engine, None)["version"] == "v1.2.3"  # the deploy's smoke test compares it with the tag
