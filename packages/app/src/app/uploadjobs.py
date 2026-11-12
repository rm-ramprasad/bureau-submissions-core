"""Uploads page, in the background (owner 2026-11-12): an upload is recorded at once (input_load, stage RECEIVED) and
processed by the worker, stage by stage, so the page can show where it is and no web request runs long enough to be
cut off:

  RECEIVED -> LOADING (sheets into staging) -> CHECKING (input_checks.yaml) -> SAVING (customer tables) -> DONE

DONE whatever the result; the row says what happened: status (LOADED / FAILED), the checks, applied_at and
apply_summary ("Not saved: <why>" when saving failed). Every failure is caught and kept with the upload.
"""

from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import Engine, insert, select, update

from app import audit
from app.configstore.settings import data_source
from app.ports.files import FileStore
from canonical.db import utcnow
from canonical.staging.checks import run_checks
from canonical.staging.tables import input_load, input_save_item
from sources.excel import load_workbook

log = logging.getLogger(__name__)

RECEIVED, LOADING, CHECKING, SAVING, DONE = "RECEIVED", "LOADING", "CHECKING", "SAVING", "DONE"
INTERRUPTED_AFTER = timedelta(minutes=10)  # a stage that has not moved for this long: the processing stopped
INTERRUPTED = "Not saved: the upload was interrupted (the server stopped while processing it). Upload it again."


def db_reason(exc: BaseException) -> str:
    """A database or code error in a line the page can show: the driver's message without its codes and prefixes."""
    orig = getattr(exc, "orig", None)  # the driver's error (pyodbc: args = (sqlstate, message))
    text = str(orig.args[-1]) if orig is not None and orig.args else str(orig or exc)
    text = re.sub(r"\[[^\]]*\]", "", text)  # [23000] [Microsoft][ODBC Driver 18 ...][SQL Server]
    text = re.sub(r"\(\d+\) \(SQL\w+\)", "", text).strip(" ('\")")
    return " ".join(text.split())[:400] or type(exc).__name__


def interrupted(row: Any) -> bool:
    """True if the upload's processing stopped without finishing (no stage change for INTERRUPTED_AFTER)."""
    stage = getattr(row, "stage", None)
    since = getattr(row, "stage_at", None) or row.loaded_at
    return stage not in (None, DONE) and since is not None and utcnow() - since > INTERRUPTED_AFTER


def record_upload(engine: Engine, store: FileStore, *, tenant_id: str, submission_type: str, data: bytes,
                  file_name: str, loaded_by: str, as_of_date: date, content_type: str) -> int:  # fmt: skip
    """Keep the workbook and record the upload (stage RECEIVED); returns its load_id."""
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    key = f"{tenant_id}/uploads/{submission_type.lower()}-{as_of_date:%Y%m%d}-{stamp}.xlsx"
    uri = store.put(key, data, content_type)
    now = utcnow()
    with engine.begin() as conn:
        key_row = (
            conn.execute(
                insert(input_load).values(
                    tenant_id=tenant_id, submission_type=submission_type, batch_id=None, file_name=file_name[:255],
                    file_uri=uri, sha256=hashlib.sha256(data).hexdigest(), status="RECEIVED", loaded_by=loaded_by,
                    loaded_at=now, as_of_date=as_of_date, stage=RECEIVED, stage_at=now,
                )
            ).inserted_primary_key
        )  # fmt: skip
    assert key_row is not None
    return int(key_row[0])


def _stage(engine: Engine, load_id: int, stage: str, **values: Any) -> None:
    with engine.begin() as conn:
        conn.execute(update(input_load).where(input_load.c.load_id == load_id)
                     .values(stage=stage, stage_at=utcnow(), **values))  # fmt: skip


def _items(engine: Engine, load_id: int, items: list[dict[str, Any]]) -> None:
    """What the save left out or noted, one row each (input_save_item; the upload's details page lists them)."""
    if not items:
        return
    with engine.begin() as conn:
        conn.execute(insert(input_save_item), [
            {"load_id": load_id, "code": i["code"], "sanction_reference_number": i.get("sanction_reference_number"),
             "account_number": i.get("account_number"), "message": str(i["message"])[:1000]}
            for i in items
        ])  # fmt: skip


@dataclass(frozen=True)
class Processed:
    load_id: int
    ran: bool  # False: not RECEIVED any more (a duplicate queue delivery): nothing done
    message: str = ""
    ok: bool = True


def process_upload(engine: Engine, store: FileStore, load_id: int) -> Processed:
    """Load, check and (types on the customer tables) save one recorded upload. Safe to call twice: only a RECEIVED
    upload is processed (claimed atomically)."""
    from canonical.master.apply import UploadConflict
    from sources.master_load import load_upload

    t = input_load.c
    with engine.begin() as conn:
        claimed = conn.execute(update(input_load).where(t.load_id == load_id, t.stage == RECEIVED)
                               .values(stage=LOADING, stage_at=utcnow())).rowcount  # fmt: skip
        row = conn.execute(select(input_load).where(t.load_id == load_id)).one_or_none()
    if not claimed or row is None:
        log.info("Upload %s is not waiting to be processed: skipped", load_id)
        return Processed(load_id, ran=False)
    kind, name, as_of = row.submission_type, row.file_name, row.as_of_date
    error = None
    applied = False
    checks = None
    try:
        data = b"".join(store.read_chunks(row.file_uri))
        result = load_workbook(engine, kind, data, tenant_id=row.tenant_id, file_name=name, loaded_by=row.loaded_by,
                               batch_id=None, file_uri=row.file_uri, as_of_date=as_of, load_id=load_id)  # fmt: skip
        if result.status != "LOADED":
            message, ok = "The workbook was not loaded: " + "; ".join(i.message for i in result.errors), False
        else:
            _stage(engine, load_id, CHECKING)
            checks = run_checks(engine, load_id, kind, as_of)
            if not checks.passed:
                message, ok = (f"{name}: {checks.blocking} row(s) fail blocking data-quality checks. Fix the workbook "
                               "and upload it again."), False  # fmt: skip
            elif data_source(engine, row.tenant_id, kind) == "master":
                _stage(engine, load_id, SAVING)
                try:
                    res = load_upload(engine, engine, row.tenant_id, kind, as_of, load_id)  # views in the app database
                except Exception as exc:  # nothing was saved (one transaction): say why, kept with the upload
                    reason = str(exc) if isinstance(exc, UploadConflict) else db_reason(exc)
                    if not isinstance(exc, UploadConflict):
                        log.exception("Saving upload %s in the customer tables failed", load_id)
                    error = reason
                    _stage(engine, load_id, SAVING, apply_summary=f"Not saved: {reason}"[:1000])
                    _items(engine, load_id, [{"code": "SAVE_FAILED", "message": f"Nothing was saved: {reason}"}])
                    message, ok = f"{name} passed the checks but was not saved in the customer tables: {reason}", False
                else:
                    left_out = sum(1 for i in res.items if i["code"] == "NO_BORROWER")
                    summary = (f"sanctions {res.sanctions_added} added, {res.sanctions_updated} updated; accounts "
                               f"{res.accounts_added} added, {res.accounts_updated} updated; {res.changes} values "
                               "changed" + (f"; {left_out} account(s) not saved" if left_out else "")
                               + ("; many values cleared" if any(i["code"] == "MANY_CLEARED" for i in res.items)
                                  else ""))  # fmt: skip
                    _items(engine, load_id, res.items)
                    _stage(engine, load_id, SAVING, applied_at=utcnow(), apply_summary=summary[:1000])
                    applied, message, ok = True, f"{name} saved in the customer tables: {summary}.", True
            else:
                message, ok = (f"{name} loaded and checked. {kind.title()} is on the source views (Admin page, data "
                               "source): attach the workbook to a request as before."), True  # fmt: skip
    except Exception as exc:  # reading the file, loading, checking: the upload says why
        log.exception("Processing upload %s failed", load_id)
        error = db_reason(exc)
        message, ok = f"{name} could not be processed: {error}", False
        _stage(engine, load_id, LOADING, apply_summary=f"Not processed: {error}"[:1000])
    _stage(engine, load_id, DONE)
    with engine.connect() as conn:
        final = conn.execute(select(input_load).where(t.load_id == load_id)).one()
    audit.record(
        engine, row.loaded_by, "DATA_UPLOADED", tenant_id=row.tenant_id, target=load_id, submission_type=kind,
        as_of_date=as_of.isoformat() if as_of else None, file_name=name, status=final.status,
        dq_blocking=checks.blocking if checks else None, dq_warnings=checks.warnings if checks else None,
        applied=applied, error=error,
    )  # fmt: skip
    return Processed(load_id, ran=True, message=message, ok=ok)
