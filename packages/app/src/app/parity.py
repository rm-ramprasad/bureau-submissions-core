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
from app.requests import RequestRepository, SubmissionRequest
from app.validation.run import validate_snapshot
from app.validation.settings import load_rule_set
from app.validation.tables import record_outcome, validation_finding, validation_summary
from canonical.db import utcnow
from canonical.schema import ENTITY_TABLES
from canonical.snapshot import SnapshotRepository
from canonical.staging.loads import loaded_workbook
from canonical.tenants import TenantRepository
from sources.extract import purge_rows, run_extract
from sources.views import compile_views

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

    def text(self) -> str:
        lines = [f"Parity check of request {self.old_batch} (old app) against the new code", ""]
        for p in self.parts:
            status = "SAME" if p.same else ("DIFFERENT" if p.differences else "NOT COMPARED")
            lines.append(f"{status:12} {p.name}: {p.compared} compared, {p.differences} different {p.note}".rstrip())
            lines += [f"             - {e}" for e in p.examples]
            if p.differences > len(p.examples):
                lines.append(f"             ... and {p.differences - len(p.examples)} more")
        lines += ["", "RESULT: " + ("IDENTICAL" if self.same else "DIFFERENCES FOUND (see above)")]
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


def compare_text_files(part: Part, old: bytes, new: bytes) -> None:
    """Byte for byte; on a difference, the differing lines (the header carries the creation date and time, so a
    header-only difference is called out as expected)."""
    part.compared += 1
    if old == new:
        return
    old_lines, new_lines = old.decode("latin-1").splitlines(), new.decode("latin-1").splitlines()
    differing = [i for i in range(max(len(old_lines), len(new_lines))) if old_lines[i : i + 1] != new_lines[i : i + 1]]
    if differing == [0]:
        part.note = "(only the header line differs: creation date/time, expected)"
        return
    for i in differing:
        part.differ(f"line {i + 1}: OLD {old_lines[i : i + 1]!r} / NEW {new_lines[i : i + 1]!r}"[:400])


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


def _parity_request(old: SubmissionRequest, batch_id: str, **changes: Any) -> SubmissionRequest:
    from dataclasses import replace

    return replace(old, batch_id=batch_id, **changes)


def run_parity(
    engine: Engine,
    old_batch: str,
    out_dir: Path,
    read_old_file: Callable[[str], bytes],
    *,
    keep: bool = False,
    clock: Callable[[], datetime] = utcnow,
) -> Report:
    """The parity report for one old validation request (see the module doc). `read_old_file(uri)` returns an old
    file's bytes (from Blob storage on Azure, or a local folder)."""
    from app.pipeline import Services, get_pipeline

    requests, snapshots = RequestRepository(engine), SnapshotRepository(engine)
    rows = [r for r in requests.list_batch(old_batch) if r.run_mode is RunMode.VALIDATE and r.snapshot_id]
    if not rows:
        raise ValueError(f"{old_batch} is not a finished validation request (no validated snapshot)")
    report = Report(old_batch)
    store = LocalFileStore(out_dir)
    services = Services(engine, engine, snapshots, store)
    tenants, bureaus = TenantRepository(engine), BureauRepository(engine)
    stamp = clock().strftime("%Y%m%d%H%M%S")
    for row in rows:
        old_sid = row.snapshot_id
        assert old_sid is not None
        tenant = tenants.get(row.tenant_id)
        load_id = loaded_workbook(engine, old_batch, row.submission_type)
        batch_id = f"parity-{old_batch}-{stamp}"[:40]
        new_sid = run_extract(
            tenant=tenant,
            compiled=compile_views(engine, row.submission_type),
            submission_type=row.submission_type,
            as_of_date=row.as_of_date,
            app_engine=engine,
            source_engine=engine,
            snapshots=snapshots,
            owner="parity",
            batch_id=batch_id,
            load_id=load_id,
            adjustments=FrozenAdjustments(engine, old_sid),
        ).snapshot_id
        try:
            validate_snapshot(engine, snapshots, new_sid, load_rule_set(engine, tenant.tenant_id, row.submission_type))
            for part in compare_snapshots(engine, old_sid, new_sid):
                part.name = f"{row.submission_type} {part.name}"
                report.parts.append(part)
            pipeline = get_pipeline(row.submission_type)
            for gen in requests.generations([old_sid]):
                if gen.status.value != "SUCCEEDED" or not gen.bureau_code or pipeline is None:
                    continue
                label = f"{row.submission_type} {gen.bureau_code} files"
                new_snap = snapshots.get(new_sid)
                result = pipeline.generate_file(
                    services,
                    tenant,
                    _parity_request(gen, f"{batch_id}-{gen.bureau_code}"[:40], snapshot_id=new_sid),
                    new_snap,
                    bureaus.get(gen.tenant_id, gen.bureau_code, gen.submission_type),
                )
                file_part = Part(f"{label}: bureau file ({gen.output_file_name})")
                try:
                    old_file = read_old_file(gen.output_file_path or "")
                except FileNotFoundError as exc:
                    file_part.note = f"NOT COMPARED: old file not readable ({exc})"
                else:
                    compare_text_files(file_part, old_file, Path(result.output_file_path or "").read_bytes())
                report.parts.append(file_part)
                if gen.excel_file_path and result.excel_file_path:
                    xl_part = Part(f"{label}: bureau Excel")
                    try:
                        compare_workbooks(
                            xl_part, read_old_file(gen.excel_file_path), Path(result.excel_file_path).read_bytes()
                        )
                    except FileNotFoundError as exc:
                        xl_part.note = f"NOT COMPARED: old file not readable ({exc})"
                    report.parts.append(xl_part)
        finally:
            if not keep:
                remove_snapshot(engine, new_sid)
    return report


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
