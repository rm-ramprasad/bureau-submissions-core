"""L4 output files (ADR 0002, migration 011_output_files).

- out_file: one row per stored output file (bureau file, bureau Excel, review Excel) with its fingerprint: SHA-256,
  size and record count. The fingerprint proves later that a stored file is the one that was generated.
- out_submission: a generated bureau file marked as submitted to the bureau (who, when, acknowledgement reference).
  Status SUBMITTED for now; ACCEPTED / REJECTED come with the bureau's response handling (requirements section 11).
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable
from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, Column, DateTime, Engine, Index, Integer, String, Table, Unicode, insert, select
from sqlalchemy.exc import IntegrityError

from canonical.db import metadata, utcnow

_PK = BigInteger().with_variant(Integer(), "sqlite")

BUREAU_FILE, BUREAU_EXCEL, REVIEW_EXCEL = "BUREAU_FILE", "BUREAU_EXCEL", "REVIEW_EXCEL"
SUBMITTED = "SUBMITTED"

out_file = Table(
    "out_file",
    metadata,
    Column("file_id", _PK, primary_key=True, autoincrement=True),
    Column("request_id", _PK, nullable=False),
    Column("snapshot_id", _PK, nullable=False),
    Column("bureau_code", Unicode(20), nullable=False),
    Column("kind", Unicode(20), nullable=False),
    Column("file_name", Unicode(255), nullable=False),
    Column("file_uri", Unicode(500), nullable=False),
    Column("sha256", String(64), nullable=False),
    Column("size_bytes", _PK, nullable=False),
    Column("record_count", Integer),
    Column("created_at", DateTime, nullable=False),
    Index("ix_out_file_request", "request_id"),
)

out_submission = Table(
    "out_submission",
    metadata,
    Column("request_id", _PK, primary_key=True, autoincrement=False),
    Column("submitted_at", DateTime, nullable=False),
    Column("submitted_by", Unicode(100), nullable=False),
    Column("bureau_code", Unicode(20), nullable=False),
    Column("ack_reference", Unicode(100)),
    Column("status", Unicode(20), nullable=False),
    Column("note", Unicode(500)),
)


class AlreadySubmitted(ValueError):
    pass


def record_file(
    engine: Engine,
    *,
    request_id: int,
    snapshot_id: int,
    bureau_code: str,
    kind: str,
    file_name: str,
    file_uri: str,
    content: bytes,
    record_count: int | None = None,
) -> str:
    """Record a stored file's fingerprint; returns its SHA-256."""
    sha = hashlib.sha256(content).hexdigest()
    with engine.begin() as conn:
        conn.execute(
            insert(out_file).values(
                request_id=request_id,
                snapshot_id=snapshot_id,
                bureau_code=bureau_code,
                kind=kind,
                file_name=file_name,
                file_uri=file_uri,
                sha256=sha,
                size_bytes=len(content),
                record_count=record_count,
                created_at=utcnow(),
            )
        )
    return sha


def bureau_files(engine: Engine, request_ids: Iterable[int]) -> dict[int, dict[str, Any]]:
    """request_id -> the bureau file's fingerprint and, if marked, its submission (for the Submitted files page)."""
    ids = list(request_ids)
    if not ids:
        return {}
    f, s = out_file.c, out_submission.c
    out: dict[int, dict[str, Any]] = {}
    with engine.connect() as conn:
        for r in conn.execute(
            select(f.request_id, f.sha256, f.size_bytes, f.record_count)
            .where(f.request_id.in_(ids), f.kind == BUREAU_FILE)
            .order_by(f.file_id)
        ):
            out.setdefault(r.request_id, {})["fingerprint"] = dict(
                sha256=r.sha256, size_bytes=r.size_bytes, record_count=r.record_count
            )
        for r in conn.execute(select(out_submission).where(s.request_id.in_(ids))):
            out.setdefault(r.request_id, {})["submission"] = dict(
                status=r.status,
                submitted_at=r.submitted_at,
                submitted_by=r.submitted_by,
                ack_reference=r.ack_reference,
                note=r.note,
            )
    return out


def mark_submitted(
    engine: Engine,
    request_id: int,
    bureau_code: str,
    user: str,
    ack_reference: str | None = None,
    note: str | None = None,
    now: datetime | None = None,
) -> None:
    """Mark a generated bureau file as submitted. Once only: raises AlreadySubmitted the second time."""
    try:
        with engine.begin() as conn:
            conn.execute(
                insert(out_submission).values(
                    request_id=request_id,
                    submitted_at=now or utcnow(),
                    submitted_by=user[:100],
                    bureau_code=bureau_code,
                    ack_reference=(ack_reference or "").strip()[:100] or None,
                    status=SUBMITTED,
                    note=(note or "").strip()[:500] or None,
                )
            )
    except IntegrityError as exc:
        raise AlreadySubmitted(f"Request {request_id} is already marked as submitted") from exc
