"""Workbook labels -> canonical values, from automn.ref_code_translation.label (the reference the NBFC maintains;
the workbooks' Master Data sheet is never read). The source views join the same table; the upload check `label`
(submissions/inputs/checks.py) uses this loader. Labels match ignoring case and leading / trailing spaces.
"""

from __future__ import annotations

from sqlalchemy import Engine, select, true

from canonical.schema import ref_code_translation


def normalise(label: str) -> str:
    return str(label).strip().upper()  # the views should match the same way: UPPER(TRIM(value))


def label_values(engine: Engine, submission_type: str, code_list: str) -> dict[str, str]:
    """normalised label -> canonical value, for the active rows of one code list that have a label."""
    t = ref_code_translation.c
    with engine.connect() as conn:
        rows = conn.execute(
            select(t.label, t.code).where(
                t.submission_type == str(submission_type),
                t.code_list == code_list,
                t.label.isnot(None),
                t.is_active == true(),
            )
        ).all()
    out: dict[str, str] = {}
    for label, code in rows:
        key = normalise(label)
        if not key:
            continue
        if key in out and out[key] != code:
            raise ValueError(
                f"automn.ref_code_translation: label {label!r} of {submission_type} {code_list} "
                f"points to both {out[key]} and {code}; a label must name one value"
            )
        out[key] = code
    return out
