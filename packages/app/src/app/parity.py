"""Parity check before go-live: the new code against what the old app produced, on the same inputs.

For each type of a finished validation request made by the old app:
  1. build a new snapshot from the same upload (load_id) and date through the source views, with the same frozen
     exclusions and overrides as the old snapshot, and validate it with the client's current rules;
  2. compare it with the old snapshot: every canonical row, every record outcome, every finding;
  3. for each bureau file the old app generated from that validation, write the new files (to a local folder) and
     compare them with the old ones: the TUDF / bureau file byte for byte, the bureau Excel cell by cell;
  4. remove the temporary snapshot (unless asked to keep it).
The result is a plain report: what was compared, how many rows, and every difference (the first 20 per part).

The temporary snapshot carries batch_id "parity-<old batch>-<time>" and no request row, so it never shows on the
pages; retention would remove it too.
"""

from __future__ import annotations

import io
from collections import Counter
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy import Engine, delete, insert, select

from app.adjustments.extract import SqlAdjustments
from app.adjustments.tables import snapshot_adjustment
from app.bureaus import BureauRepository
from app.enums import RunMode
from app.ports.files import LocalFileStore
from app.requests import RequestRepository
from app.validation.tables import record_outcome, validation_finding, validation_summary
from canonical.db import utcnow
from canonical.schema import ENTITY_TABLES
from canonical.snapshot import SnapshotRepository
from canonical.tenants import TenantRepository
from sources.extract import purge_rows

SHOWN = 20  # differences listed per part (all are counted)
FINDING_KEY = ("rule_id", "entity", "account_key", "party_key", "field", "outcome", "value")


@dataclass
class Part:
    name: str
    compared: int = 0
    differences: int = 0
    examples: list[str] = field(default_factory=list)
    note: str = ""

    def differ(self, text: str) -> None:
        self.differences += 1
        if len(self.examples) < SHOWN:
            self.examples.append(text)

    @property
    def same(self) -> bool:
        return self.differences == 0 and not self.note.startswith("NOT COMPARED")


@dataclass
class Report:
    old_batch: str
    parts: list[Part] = field(default_factory=list)

    @property
    def same(self) -> bool:
        return all(p.same for p in self.parts)

    @property
    def different(self) -> bool:
        return any(p.differences for p in self.parts)

    def text(self) -> str:
        lines = [f"Parity check of request {self.old_batch} (old app) against the new code", ""]
        for p in self.parts:
            status = "SAME" if p.same else ("DIFFERENT" if p.differences else "NOT COMPARED")
            lines.append(f"{status:12} {p.name}: {p.compared} compared, {p.differences} different {p.note}".rstrip())
            lines += [f"             - {e}" for e in p.examples]
            if p.differences > len(p.examples):
                lines.append(f"             ... and {p.differences - len(p.examples)} more")
        if self.same:
            result = "IDENTICAL"
        elif self.different:
            result = "DIFFERENCES FOUND (see above)"
        else:
            result = "INCOMPLETE: no differences, but some files were not compared (see NOT COMPARED above)"
        lines += ["", "RESULT: " + result]
        return "\n".join(lines)


class FrozenAdjustments(SqlAdjustments):
    """Applies the old snapshot's frozen adjustments (the same exclusions and overrides) to the parity snapshot."""

    def __init__(self, engine: Engine, old_snapshot_id: int) -> None:
        super().__init__(engine)
        self.old_snapshot_id = old_snapshot_id

    def prepare(self, snapshot_id: int, sql: dict[str, str], run: dict[str, Any]) -> int:
        from sqlalchemy import text

        from app.adjustments import sql as adjustment_sql

        cols = [c for c in snapshot_adjustment.c if c.name != "snapshot_id"]
        with self.engine.begin() as conn:
            rows = conn.execute(select(*cols).where(snapshot_adjustment.c.snapshot_id == self.old_snapshot_id)).all()
            if rows:
                conn.execute(insert(snapshot_adjustment), [{**r._mapping, "snapshot_id": snapshot_id} for r in rows])
        if rows:
            params = {"snapshot_id": snapshot_id, **run}
            with self.engine.begin() as conn:
                for stmt in adjustment_sql.resolve_sql(sql["account"], sql["account_holder"]):
                    conn.execute(text(stmt), params)
        return len(rows)


def _rows(engine: Engine, table: Any, snapshot_id: int, columns: list[str]) -> Counter[tuple[str, ...]]:
    with engine.connect() as conn:
        result = conn.execute(select(*(table.c[c] for c in columns)).where(table.c.snapshot_id == snapshot_id))
        return Counter(tuple("" if v is None else str(v) for v in r) for r in result)


def _compare_counters(
    part: Part, old: Counter[tuple[str, ...]], new: Counter[tuple[str, ...]], labels: list[str]
) -> None:
    part.compared += sum(old.values())
    for row in (old - new).elements():
        part.differ("only in OLD: " + ", ".join(f"{k}={v}" for k, v in zip(labels, row, strict=True) if v))
    for row in (new - old).elements():
        part.differ("only in NEW: " + ", ".join(f"{k}={v}" for k, v in zip(labels, row, strict=True) if v))


def compare_snapshots(engine: Engine, old_id: int, new_id: int) -> list[Part]:
    """Canonical rows per entity (row ids and snapshot ids left out), record outcomes, findings."""
    parts = []
    for entity, table in ENTITY_TABLES.items():
        cols = [c.name for c in table.c if c.name not in ("row_id", "snapshot_id")]
        old, new = _rows(engine, table, old_id, cols), _rows(engine, table, new_id, cols)
        if old or new:
            part = Part(f"snapshot rows: {entity}")
            _compare_counters(part, old, new, cols)
            parts.append(part)
    cols = ["account_key", "party_key", "outcome", "reason_rule_id"]
    part = Part("record outcomes (reported / not reported)")
    _compare_counters(
        part, _rows(engine, record_outcome, old_id, cols), _rows(engine, record_outcome, new_id, cols), cols
    )
    parts.append(part)
    part = Part("validation findings")
    _compare_counters(
        part,
        _rows(engine, validation_finding, old_id, list(FINDING_KEY)),
        _rows(engine, validation_finding, new_id, list(FINDING_KEY)),
        list(FINDING_KEY),
    )
    parts.append(part)
    return parts


def compare_text_files(part: Part, old: bytes, new: bytes, header_bytes: int | None = None) -> None:
    """Byte for byte. The header carries the creation date and time, so a difference only inside the header (the
    first `header_bytes`, or the first line when None) is noted as expected; any other difference is listed with
    its position and the bytes around it."""
    part.compared += 1
    if old == new:
        return
    if header_bytes is None:
        header_bytes = old.find(b"\n") + 1 if b"\n" in old else 0
    n = min(len(old), len(new))
    first = next((i for i in range(n) if old[i] != new[i]), n)
    tail_same = len(old) == len(new) and old[header_bytes:] == new[header_bytes:]
    if header_bytes and first < header_bytes and tail_same:
        part.note = "(only the header differs: creation date/time, expected)"
        return
    offsets = [i for i in range(max(header_bytes, first), n) if old[i] != new[i]]
    if len(old) != len(new):
        part.differ(f"length: OLD {len(old)} bytes / NEW {len(new)} bytes")
    i = 0
    while i < len(offsets):  # one difference per stretch of differing bytes (the first SHOWN listed)
        at = offsets[i]
        a, b = old[max(0, at - 20) : at + 40], new[max(0, at - 20) : at + 40]
        part.differ(f"byte {at}: OLD {a.decode('latin-1')!r} / NEW {b.decode('latin-1')!r}")
        while i < len(offsets) and offsets[i] < at + 40:
            i += 1


def compare_workbooks(part: Part, old: bytes, new: bytes, sheet: str | None = None) -> None:
    """Cell values of one sheet (the first if not named)."""
    from openpyxl import load_workbook

    a, b = load_workbook(io.BytesIO(old), read_only=True), load_workbook(io.BytesIO(new), read_only=True)
    wa, wb = (a[sheet], b[sheet]) if sheet else (a.worksheets[0], b.worksheets[0])
    ra, rb = list(wa.iter_rows(values_only=True)), list(wb.iter_rows(values_only=True))
    part.compared += len(ra)
    for i in range(max(len(ra), len(rb))):
        x, y = ra[i] if i < len(ra) else None, rb[i] if i < len(rb) else None
        if x != y:
            part.differ(f"row {i + 1}: OLD {x!r} / NEW {y!r}"[:400])


def _old_load_id(engine: Engine, old_batch: str, submission_type: str, built_at: datetime) -> int | None:
    """The upload the old snapshot was built from: the request's newest LOADED workbook of the type loaded before
    the snapshot (a later re-upload would otherwise be picked)."""
    from canonical.staging.tables import input_load as t

    with engine.connect() as conn:
        return conn.execute(
            select(t.c.load_id)
            .where(
                t.c.batch_id == old_batch,
                t.c.submission_type == str(submission_type),
                t.c.status == "LOADED",
                t.c.loaded_at <= built_at,
            )
            .order_by(t.c.loaded_at.desc(), t.c.load_id.desc())
            .limit(1)
        ).scalar()


def run_parity(
    engine: Engine,
    old_batch: str,
    out_dir: Path,
    read_old_file: Callable[[str], bytes],
    *,
    source_engine: Engine | None = None,
    keep: bool = False,
    clock: Callable[[], datetime] = utcnow,
) -> Report:
    """The parity report for one old validation request (see the module doc). `read_old_file(uri)` returns an old
    file's bytes (from Blob storage on Azure, or a local folder). `source_engine`: where the source views are
    (default: the app database)."""
    import secrets
    from dataclasses import replace

    from app.enums import RequestStatus, SnapshotStatus
    from app.pipeline import Services, get_pipeline

    requests, snapshots = RequestRepository(engine), SnapshotRepository(engine)
    rows = [
        r
        for r in requests.list_batch(old_batch)
        if r.run_mode is RunMode.VALIDATE and r.status is RequestStatus.SUCCEEDED and r.snapshot_id
    ]
    rows = [r for r in rows if snapshots.get(r.snapshot_id or 0).status is SnapshotStatus.VALIDATED]
    if not rows:
        raise ValueError(f"{old_batch} is not a finished validation request (no validated snapshot)")
    report = Report(old_batch)
    services = Services(engine, source_engine or engine, snapshots, LocalFileStore(out_dir))
    tenants, bureaus = TenantRepository(engine), BureauRepository(engine)
    batch_id = f"parity-{clock():%Y%m%d%H%M%S}-{secrets.token_hex(3)}"  # unique, within the 40-character limit
    for row in rows:
        old_sid = row.snapshot_id
        assert old_sid is not None
        old_snap = snapshots.get(old_sid)
        tenant = tenants.get(row.tenant_id)
        pipeline = get_pipeline(row.submission_type)
        if pipeline is None:
            raise ValueError(f"No pipeline for {row.submission_type}")
        new_sid = None
        try:
            new_sid, _ = pipeline.build_snapshot(  # the workers' own code path
                services,
                tenant,
                replace(row, batch_id=batch_id),
                "parity",
                load_id=_old_load_id(engine, old_batch, row.submission_type, old_snap.created_at),
                adjustments=FrozenAdjustments(engine, old_sid),
            )
            for part in compare_snapshots(engine, old_sid, new_sid):
                part.name = f"{row.submission_type} {part.name}"
                report.parts.append(part)
            header = _header_bytes(row.submission_type)
            for gen in requests.generations([old_sid]):
                if gen.status is not RequestStatus.SUCCEEDED or not gen.bureau_code:
                    continue
                label = f"{row.submission_type} {gen.bureau_code} files"
                result = pipeline.generate_file(
                    services,
                    tenant,
                    replace(gen, batch_id=f"{batch_id}-{gen.request_id}"[:40], snapshot_id=new_sid),
                    snapshots.get(new_sid),
                    bureaus.get(gen.tenant_id, gen.bureau_code, gen.submission_type),
                )
                file_part = Part(f"{label}: bureau file ({gen.output_file_name})")
                try:
                    old_file = read_old_file(gen.output_file_path or "")
                except FileNotFoundError as exc:
                    file_part.note = f"NOT COMPARED: old file not readable ({exc})"
                else:
                    compare_text_files(file_part, old_file, Path(result.output_file_path or "").read_bytes(), header)
                report.parts.append(file_part)
                if gen.excel_file_path and result.excel_file_path:
                    xl_part = Part(f"{label}: bureau Excel")
                    try:
                        old_xl = read_old_file(gen.excel_file_path)
                    except FileNotFoundError as exc:
                        xl_part.note = f"NOT COMPARED: old file not readable ({exc})"
                    else:
                        compare_workbooks(xl_part, old_xl, Path(result.excel_file_path).read_bytes())
                    report.parts.append(xl_part)
        finally:
            if new_sid is None:  # the build failed: its FAILED header (if any) is removed too
                failed = snapshots.find_for_batch(batch_id, row.tenant_id, row.submission_type)
                new_sid = failed.snapshot_id if failed else None
            if new_sid is not None and not keep:
                remove_snapshot(engine, new_sid)
    return report


def _header_bytes(submission_type: str) -> int | None:
    """The TUDF header's fixed length (a consumer TUDF has no line breaks); None: the first line (pipe files)."""
    if str(submission_type) == "CONSUMER":
        from app.types.consumer.file import header_length

        return header_length()
    return None


def remove_snapshot(engine: Engine, snapshot_id: int) -> None:
    """Delete a parity snapshot completely (rows, results, adjustments, header)."""
    purge_rows(engine, snapshot_id)
    SqlAdjustments(engine).purge(snapshot_id)
    from canonical.schema import snapshot_code_translation

    with engine.begin() as conn:
        for t in (validation_finding, validation_summary, record_outcome, snapshot_code_translation):
            conn.execute(delete(t).where(t.c.snapshot_id == snapshot_id))
    SnapshotRepository(engine).delete(snapshot_id)


def local_reader(folder: Path) -> Callable[[str], bytes]:
    """Old files from a local folder (downloaded from the Files page): matched by file name."""

    def read(uri: str) -> bytes:
        name = uri.rstrip("/").rsplit("/", 1)[-1].split("?", 1)[0]
        path = folder / name
        if not path.is_file():
            raise FileNotFoundError(f"{name} not in {folder}")
        return path.read_bytes()

    return read


def iter_parts(report: Report) -> Iterator[Part]:
    yield from report.parts
