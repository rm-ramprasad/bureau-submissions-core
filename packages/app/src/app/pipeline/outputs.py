"""The three outputs of one bureau, for types with an Excel layout (types/<type>/excel_layout.yaml, e.g. consumer):

  1. bureau Excel   <stem>.xlsx         the Data Submission Form only: what is uploaded to the bureau's portal
  2. review Excel   <stem>-review.xlsx  the same form + Reconciliation, Column map, Sample, Mismatches, Findings,
                                        Left out, Data quality: for the credit team
  3. TUDF           <stem>.txt          converted from file 1 by the standalone converter (bureau_core.tudf)

File 3 is made from file 1, and the reconciliation (file 1 vs file 3, cell by cell at its TUDF place) goes into file 2.
If it does not match, only file 2 is stored and the request fails (OutputsNotMatched carries file 2's location).
"""

from __future__ import annotations

import hashlib
import io
from collections import Counter
from datetime import datetime
from pathlib import Path

import yaml
from sqlalchemy import select

from app.adjustments.summary import adjustments_csv
from app.bureaus import BureauProfile
from app.enums import RecordOutcome
from app.render import build_excel
from app.requests import RequestResult, SubmissionRequest
from app.validation.tables import record_outcome, validation_finding
from bureau_core.formats.excel.engine import add_sheets
from bureau_core.tudf import convert
from bureau_core.tudf.reconcile import reconcile
from bureau_core.types import type_definition
from canonical.schema import ENTITY_TABLES
from canonical.snapshot import Snapshot
from canonical.staging.loads import loaded_workbook
from canonical.staging.tables import input_issue

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


class OutputsNotMatched(Exception):
    """The TUDF does not reconcile with the bureau Excel: only the review Excel was stored."""

    def __init__(self, message: str, review_file_path: str):
        super().__init__(message)
        self.review_file_path = review_file_path


def _findings(engine, snapshot_id: int) -> list[list]:
    f = validation_finding.c
    rows = [["Rule", "Outcome", "Record type", "Account", "Borrower", "Field", "Value", "Message"]]
    with engine.connect() as conn:
        rows += [
            list(r)
            for r in conn.execute(
                select(f.rule_id, f.outcome, f.entity, f.account_key, f.party_key, f.field, f.value, f.message)
                .where(f.snapshot_id == snapshot_id)
                .order_by(f.rule_id, f.account_key, f.party_key)
            )
        ]
    return rows


def _left_out(engine, snapshot_id: int) -> list[list]:
    """Records validation rejected (not in the bureau Excel or the TUDF), with the borrower's name and the reason."""
    o, party, f = record_outcome.c, ENTITY_TABLES["party"].c, validation_finding.c
    rows = [["Account", "Borrower", "Name", "Reason rule", "Reason"]]
    with engine.connect() as conn:
        names = dict(
            conn.execute(select(party.party_key, party.full_name).where(party.snapshot_id == snapshot_id)).all()
        )
        messages = dict(conn.execute(select(f.rule_id, f.message).where(f.snapshot_id == snapshot_id)).all())
        for r in conn.execute(
            select(o.account_key, o.party_key, o.reason_rule_id)
            .where(o.snapshot_id == snapshot_id, o.outcome == str(RecordOutcome.REJECTED))
            .order_by(o.account_key, o.party_key)
        ):
            rows.append(
                [
                    r.account_key,
                    r.party_key,
                    names.get(r.party_key) or "",
                    r.reason_rule_id or "",
                    messages.get(r.reason_rule_id) or "",
                ]
            )
    return rows


def _data_quality(engine, batch_id: str | None, submission_type: str) -> list[list]:
    """The upload checks' results for the request's workbook."""
    rows = [["Severity", "Check", "Sheet", "Excel row", "Column", "Message"]]
    load_id = loaded_workbook(engine, batch_id, submission_type)
    if load_id is None:
        return [*rows, ["", "", "", "", "", "No uploaded workbook for this request"]]
    i = input_issue.c
    with engine.connect() as conn:
        rows += [
            list(r)
            for r in conn.execute(
                select(i.severity, i.code, i.sheet, i.source_row, i.column_name, i.message)
                .where(i.load_id == load_id)
                .order_by(i.issue_id)
            )
        ]
    return rows


def _loans(engine, snapshot_id: int) -> list[list]:
    """Every loan of the snapshot with its borrower and whether it is in the file (left out: the rule and reason)."""
    o, party, f = record_outcome.c, ENTITY_TABLES["party"].c, validation_finding.c
    rows = [["Account", "Borrower", "Name", "In the file", "Reason rule", "Reason"]]
    with engine.connect() as conn:
        names = dict(
            conn.execute(select(party.party_key, party.full_name).where(party.snapshot_id == snapshot_id)).all()
        )
        messages = dict(conn.execute(select(f.rule_id, f.message).where(f.snapshot_id == snapshot_id)).all())
        for r in conn.execute(
            select(o.account_key, o.party_key, o.outcome, o.reason_rule_id)
            .where(o.snapshot_id == snapshot_id)
            .order_by(o.account_key, o.party_key)
        ):
            rejected = r.outcome == str(RecordOutcome.REJECTED)
            rows.append(
                [
                    r.account_key,
                    r.party_key,
                    names.get(r.party_key) or "",
                    "no" if rejected else "yes",
                    r.reason_rule_id or "" if rejected else "",
                    messages.get(r.reason_rule_id) or "" if rejected else "",
                ]
            )
    return rows


def _adjustments(engine, snapshot_id: int) -> list[list]:
    """The user's exclusions and overrides applied to this snapshot (old -> new); header only when there are none."""
    import csv

    text = adjustments_csv(engine, snapshot_id)
    rows = [r for r in csv.reader(io.StringIO(text)) if r and not r[0].startswith("#")]
    return rows or [
        [
            "adjustment_id",
            "kind",
            "entered_key",
            "effect",
            "account_key",
            "party_key",
            "field",
            "old_value",
            "new_value",
            "reason",
            "entered_by",
        ]
    ]


def _workbook(sheets: dict[str, list[list]]) -> bytes:
    """A new workbook of these sheets (name -> rows); header rows bold, as add_sheets writes them."""
    from openpyxl import Workbook

    wb = Workbook()
    wb.active.title = "_"
    buf = io.BytesIO()
    wb.save(buf)
    content = add_sheets(buf.getvalue(), sheets)
    from openpyxl import load_workbook

    wb = load_workbook(io.BytesIO(content))
    del wb["_"]
    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()


def _file_summary(content: bytes, name: str, bureau: BureauProfile, snapshot: Snapshot, delimiter: str) -> list[list]:
    """Read from the file itself: one line per segment, the tag is the first field."""
    lines = [ln for ln in content.decode("ascii", errors="replace").splitlines() if ln]
    tags = Counter(ln.split(delimiter, 1)[0] for ln in lines)
    rows = [
        ["Item", "Value"],
        ["File", name],
        ["Bureau", str(bureau.bureau_code)],
        ["Member ID", bureau.member_id or ""],
        ["As of", f"{snapshot.as_of_date:%d-%m-%Y}"],
        ["Size (bytes)", len(content)],
        ["SHA-256", hashlib.sha256(content).hexdigest()],
        ["Lines", len(lines)],
        ["", ""],
        ["Segment", "Lines"],
    ]
    return rows + [[tag, n] for tag, n in tags.items()]


def generate_file_with_review(
    services, tenant, request: SubmissionRequest, snapshot: Snapshot, bureau: BureauProfile, write_file
) -> RequestResult:
    """Types without an Excel layout (commercial): the bureau file from its engine, plus a review Excel (Summary, Loans,
    Findings, Data quality) for the credit team. The file is never changed by the review."""
    created = datetime.now()
    content, stats = write_file(services, request, snapshot, bureau, created)
    if stats.written == 0:
        raise ValueError(
            f"No accepted records to submit: all {stats.total_records} records were rejected by "
            f"validation. Review the findings of snapshot {snapshot.snapshot_id} (no file written)."
        )
    name = bureau.file_name(created, snapshot.as_of_date, request.reporting_cycle)
    if Path(name).name != name:
        raise ValueError(f"File name {name!r} from the bureau profile must not contain a path")
    engine, folder, stem = services.app_engine, f"{tenant.tenant_id}/{request.batch_id}", Path(name).stem
    submission_type = str(request.submission_type)
    layout = yaml.safe_load(type_definition(submission_type).path("layout.yaml").read_text(encoding="utf-8"))
    delimiter = (layout.get("encoding") or {}).get("delimiter", "|")
    review = _workbook(
        {
            "Summary": _file_summary(content, name, bureau, snapshot, delimiter),
            "Loans": _loans(engine, snapshot.snapshot_id),
            "Findings": _findings(engine, snapshot.snapshot_id),
            "Exceptions applied": _adjustments(engine, snapshot.snapshot_id),
            "Data quality": _data_quality(engine, snapshot.batch_id, submission_type),
        }
    )
    file_uri = services.store.put(f"{folder}/{name}", content, "text/plain")  # never overwrites
    review_uri = services.store.put(f"{folder}/{stem}-review.xlsx", review, XLSX)
    return RequestResult(
        output_file_name=name,
        output_file_path=file_uri,
        review_file_path=review_uri,
        total_records=stats.total_records,
        accepted_records=stats.written,
        rejected_records=stats.rejected,
        duplicate_records=stats.duplicates,
    )


def generate_outputs(
    services, tenant, request: SubmissionRequest, snapshot: Snapshot, bureau: BureauProfile
) -> RequestResult:
    created = datetime.now()
    tudf_name = bureau.file_name(created, snapshot.as_of_date, request.reporting_cycle)
    if Path(tudf_name).name != tudf_name:
        raise ValueError(f"File name {tudf_name!r} from the bureau profile must not contain a path")
    stem = Path(tudf_name).stem
    engine, folder = services.app_engine, f"{tenant.tenant_id}/{request.batch_id}"
    submission_type = str(request.submission_type)

    excel = build_excel(engine, snapshot.snapshot_id, bureau, submission_type)
    if excel.rows == 0:
        raise ValueError(
            f"No accepted records to submit: all {excel.rejected} records were rejected by validation. "
            f"Review the findings of snapshot {snapshot.snapshot_id} (no file written)."
        )
    tudf = convert(
        excel.content,
        submission_type=submission_type,
        bureau=str(bureau.bureau_code),
        member_id=bureau.member_id,
        member_short_name=bureau.member_short_name,
        header_tag=bureau.header_segment_tag,
        as_of_date=snapshot.as_of_date,
        cycle=str(request.reporting_cycle) if request.reporting_cycle else None,
    )
    recon = reconcile(excel.content, tudf.content, submission_type)
    sheets = recon.sheets()
    sheets["Findings"] = _findings(engine, snapshot.snapshot_id)
    sheets["Left out"] = _left_out(engine, snapshot.snapshot_id)
    sheets["Exceptions applied"] = _adjustments(engine, snapshot.snapshot_id)
    sheets["Data quality"] = _data_quality(engine, snapshot.batch_id, submission_type)
    if tudf.warnings:
        sheets["Conversion warnings"] = [["Warning"]] + [[w] for w in tudf.warnings]
    review_uri = services.store.put(f"{folder}/{stem}-review.xlsx", add_sheets(excel.content, sheets), XLSX)
    if not recon.matched:
        mismatched = sum(c.mismatched for c in recon.columns) + sum(1 for c in recon.checks if not c[3])
        raise OutputsNotMatched(
            f"Reconciliation NOT MATCHED for {bureau.bureau_code}: {mismatched} problem(s) "
            "between the bureau Excel and the TUDF. Only the review Excel was kept; see its "
            "Reconciliation and Mismatches sheets.",
            review_uri,
        )
    excel_uri = services.store.put(f"{folder}/{stem}.xlsx", excel.content, XLSX)  # never overwrites
    tudf_uri = services.store.put(f"{folder}/{tudf_name}", tudf.content, "text/plain")
    return RequestResult(
        output_file_name=tudf_name,
        output_file_path=tudf_uri,
        excel_file_path=excel_uri,
        review_file_path=review_uri,
        total_records=excel.rows + excel.rejected,
        accepted_records=tudf.stats.written,
        rejected_records=excel.rejected,
        duplicate_records=tudf.stats.duplicates,
    )
