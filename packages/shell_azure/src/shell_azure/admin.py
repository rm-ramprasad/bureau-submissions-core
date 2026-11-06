"""Admin jobs: run by the Function App's admin endpoint (POST /ops/<job>, function key; deploy/ops.ps1), by its
timers (recovery, retention) and locally (python -m shell_azure.admin <job> ...).

  migrate                 apply pending migrations, seed reference data, add missing placeholder bureau profiles
  users <args>            add / email / disable / enable / list app users
  views <TYPE> --as-of D --load-id N   read-only check of a type's source views for one upload and date
  recovery                release or fail stuck runs, re-dispatch PENDING rows never picked up
  retention [--days N] [--dry-run]     remove old personal data and files
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import sys
from collections.abc import Callable
from datetime import date, timedelta
from typing import Any

from sqlalchemy import Engine

from app import audit
from app.ports.files import FileStore
from app.ports.jobs import Dispatcher
from app.users import UserError, UserRepository
from canonical.db import utcnow

JOBS = ("migrate", "users", "views", "recovery", "retention")
RUN_TIMEOUT = timedelta(minutes=40)  # longer than host.json functionTimeout (30 min): the run is dead by then
PENDING_GRACE = timedelta(minutes=10)


def migrate(engine: Engine, tenant: str | None) -> dict[str, Any]:
    from app.bureaus import BureauRepository
    from app.bureaus.seed import seed_missing
    from canonical.migrate import apply_migrations
    from canonical.reference import seed

    result: dict[str, Any] = {"applied": apply_migrations(engine), "reference": seed(engine)}
    if tenant:  # placeholder profiles it lacks; never edits existing (real) ones
        result["bureau_profiles_added"] = seed_missing(BureauRepository(engine), tenant)
    return result


def users(engine: Engine, args: list[str], actor: str = "admin") -> dict[str, Any]:
    """The reference's `python -m submissions.users` command line, as a job."""
    ap = argparse.ArgumentParser(prog="users", description="Manage app users (SSO)", exit_on_error=False)
    sub = ap.add_subparsers(dest="cmd", required=True)
    add = sub.add_parser("add")
    add.add_argument("username")
    add.add_argument("--email", required=True)
    add.add_argument("--name", required=True)
    add.add_argument("--admin", action="store_true")
    em = sub.add_parser("email")
    em.add_argument("username")
    em.add_argument("email")
    for name in ("disable", "enable"):
        sub.add_parser(name).add_argument("username")
    sub.add_parser("list")
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            a = ap.parse_args(args)
        except (SystemExit, argparse.ArgumentError) as exc:
            code = exc.code if isinstance(exc, SystemExit) and isinstance(exc.code, int) else 2
            return {"exit_code": code, "output": (out.getvalue() + err.getvalue()) or str(exc)}
    repo = UserRepository(engine)
    try:
        if a.cmd == "list":
            lines = [
                f"{u.username:20} {u.display_name:30} {u.email or '(no e-mail)':35} "
                f"{'active' if u.is_active else 'disabled':8} {'admin' if u.is_admin else '':5} "
                f"last login {u.last_login_at or '-'}"
                for u in repo.list()
            ]
            return {"exit_code": 0, "output": "\n".join(lines)}
        if a.cmd == "add":
            repo.add(a.username, a.name, a.email, a.admin)
            audit.record(engine, actor, "USER_ADDED", target=a.username.lower(), email=a.email.lower(), admin=a.admin)
        elif a.cmd == "email":
            repo.set_email(a.username, a.email)
            audit.record(engine, actor, "USER_EMAIL_CHANGED", target=a.username.lower(), email=a.email.lower())
        else:
            repo.set_active(a.username, a.cmd == "enable")
            audit.record(engine, actor, f"USER_{a.cmd.upper()}D", target=a.username.lower())
    except UserError as exc:
        return {"exit_code": 1, "output": f"error: {exc}"}
    return {"exit_code": 0, "output": f"{a.cmd}: {a.username.lower()} done"}


def views(engine: Engine, submission_type: str, as_of: date, load_id: int) -> dict[str, Any]:
    from sources.views import check_views

    rows, errors, warnings = check_views(engine, submission_type.upper(), as_of, load_id)
    return {"ok": not errors, "rows": rows, "errors": errors, "warnings": warnings}


def recovery(engine: Engine, dispatcher: Dispatcher) -> dict[str, Any]:
    from app.ops.recovery import RecoverySettings, recover
    from app.requests import RequestRepository
    from canonical.snapshot import SnapshotRepository

    requests = RequestRepository(engine)
    result = recover(
        engine,
        requests,
        SnapshotRepository(engine),
        RecoverySettings(run_timeout=RUN_TIMEOUT, build_timeout=RUN_TIMEOUT),
    )
    stale = requests.pending_before(utcnow() - PENDING_GRACE)
    dispatcher.dispatch(stale)
    return {
        "released": result.released,
        "failed": result.failed,
        "snapshots_failed": result.snapshots_failed,
        "redispatched": [r.request_id for r in stale],
    }


def retention(engine: Engine, store: FileStore, days: int, dry_run: bool = False) -> dict[str, Any]:
    from app.ops.retention import purge

    if days < 1:
        raise ValueError("days must be at least 1")
    result = purge(engine, timedelta(days=days), store, dry_run=dry_run, actor="retention")
    return {"snapshots": result.snapshots, "input_loads": result.loads, "files": len(result.files), "dry_run": dry_run}


def run_job(
    event: dict[str, Any],
    engine: Engine,
    *,
    source_engine: Engine | None = None,
    tenant: str | None,
    dispatcher: Callable[[], Dispatcher],
    store: Callable[[], FileStore],
    retention_days: int = 90,
    actor: str = "admin-function",
) -> dict[str, Any]:
    """One job from an event: {"job": ..., "args": [...], "days": N, "dry_run": bool}. ValueError for bad input."""
    job = (event or {}).get("job")
    args = [str(a) for a in event.get("args") or []]
    if job == "migrate":
        return migrate(engine, tenant)
    if job == "users":
        return users(engine, args, actor)
    if job == "views":
        ap = argparse.ArgumentParser(prog="views", exit_on_error=False)
        ap.add_argument("submission_type")
        ap.add_argument("--as-of", type=date.fromisoformat, required=True)
        ap.add_argument("--load-id", type=int, required=True)
        try:
            a = ap.parse_args(args)
        except (SystemExit, argparse.ArgumentError) as exc:
            raise ValueError(f"views <TYPE> --as-of YYYY-MM-DD --load-id N ({exc})") from None
        return views(source_engine or engine, a.submission_type, a.as_of, a.load_id)  # where the views are
    if job == "recovery":
        return recovery(engine, dispatcher())
    if job == "retention":
        return retention(engine, store(), int(event.get("days", retention_days)), bool(event.get("dry_run")))
    raise ValueError(f"Unknown job {job!r}: use {', '.join(JOBS)}")


def main(argv: list[str] | None = None) -> int:
    """Run a job locally against the database in the environment (SUBMISSIONS_SQL_ENV_FILE or _DATABASE_URL)."""
    from shell_azure import handlers

    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] not in JOBS:
        print(__doc__)
        return 2
    event: dict[str, Any] = {"job": argv[0], "args": argv[1:]}
    if argv[0] == "retention":
        ap = argparse.ArgumentParser(prog="retention")
        ap.add_argument("--days", type=int)
        ap.add_argument("--dry-run", action="store_true")
        a = ap.parse_args(argv[1:])
        event = {"job": "retention", "dry_run": a.dry_run, **({"days": a.days} if a.days is not None else {})}
    try:
        result = handlers.jobs(event, actor="admin-cli")
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(result["output"] if "output" in result else json.dumps(result, indent=2, default=str))
    return int(result.get("exit_code", 0) or (0 if result.get("ok", True) else 1))


if __name__ == "__main__":
    raise SystemExit(main())
