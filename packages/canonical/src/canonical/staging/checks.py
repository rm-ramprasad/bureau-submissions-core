"""Data-quality checks on an uploaded workbook, run on its staging rows right after it loads (before submit).

The checks are data: canonical/types/<type>/input_checks.yaml.

  sheet: Data Submission Form        # the staging table of this sheet (inputs.yaml)
  checks:                            # several sheets: sheets: [{sheet: BS, checks: [...]}, {sheet: AS, ...}]
    - id: PAN_FORMAT                 # reported as DQ.<id>
      check: pattern                 # one of CHECKS below
      columns: [income_tax_id_number]
      pattern: "^[A-Z]{5}[0-9]{4}[A-Z]$"
      severity: warn                 # block: the request cannot be submitted until a corrected workbook passes
      message: PAN is not in the AAAAA9999A format

Check kinds (`columns` are staging columns of the sheet):
  required         each listed column has a value
  any_of           at least one of the columns has a value
  duplicate        no two rows share the values of `columns` (all columns of the sheet when `columns: all`)
  unique_where     at most one row per `columns` value among rows where `where` (column: value) holds
  consistent       rows with the same `by` value (e.g. an account) have the same value in each of `columns`
                   (account-level fields repeated on every borrower row); the first row's value is the one used
  pattern          non-empty values match the regular expression `pattern`
  date             non-empty values are real dates, DDMMYYYY (or YYYY-MM-DD when the cell was an Excel date)
  code             non-empty values are active file codes of `code_list` (types/<type>/codes.yaml)
  pin_state        the PIN in `pin` belongs to the state code in `state` (ref_state_pin)
  min_age          the date in `columns` is at least `years` before the request's as-of date
  not_before       the date in `columns` is not before the date in `other` (both present)
  not_after_as_of  the date in `columns` is not after the request's as-of date
  in_source        each value of `columns[0]` is returned by `sql` (bound :as_of_date), e.g. the account is in the
                   source view for the request's date
  in_sheet         each value of `columns[0]` is in column `other_column` of sheet `other_sheet` of the same upload
  (in_source, in_sheet and consistent's `by` take `match: alnum`: compare letters and digits only, ignoring case,
   e.g. F012/2025-26 = F012-2025-26; the source views join the same way, automn.fn_ref_key)
  label            non-empty values are active labels of `code_list` in automn.ref_code_translation (the reference
                   the NBFC maintains; case and outer spaces ignored). No labels for the list at all: one issue
  label_matches_code  the label in `columns[0]` and the file code in `code` name the same value of `code_list`

Every problem becomes an input_issue row (ERROR for block, WARN for warn) with the Excel row and column. Any block
problem marks the upload DQ_FAILED: its rows stay in staging for review, but the request cannot use it.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date
from functools import cache

import yaml
from sqlalchemy import Engine, insert, select, text, update

from bureau_core.text import as_date
from canonical.staging.spec import load_spec, type_file
from canonical.staging.tables import input_issue, input_load, staging_tables

MAX_ISSUES_PER_CHECK = 200  # issues listed per check; the rest are counted in one summary issue
CHECKS = {
    "required",
    "any_of",
    "duplicate",
    "unique_where",
    "consistent",
    "pattern",
    "date",
    "code",
    "pin_state",
    "min_age",
    "not_before",
    "not_after_as_of",
    "in_source",
    "in_sheet",
    "label",
    "label_matches_code",
}
SEVERITY = {"block": "ERROR", "warn": "WARN"}
DDMMYYYY = re.compile(r"^\d{8}$")
ISO = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class CheckDefinitionError(ValueError):
    pass


@dataclass(frozen=True)
class Check:
    id: str
    check: str
    columns: tuple[str, ...]
    severity: str
    message: str
    options: dict = field(default_factory=dict)


@dataclass(frozen=True)
class SheetChecks:
    sheet: str
    checks: tuple[Check, ...]


@dataclass(frozen=True)
class CheckSet:
    submission_type: str
    sheets: tuple[SheetChecks, ...]

    @property
    def sheet(self) -> str:  # the first (for a one-sheet type, the only) sheet
        return self.sheets[0].sheet

    @property
    def checks(self) -> tuple[Check, ...]:
        return tuple(c for s in self.sheets for c in s.checks)


@dataclass
class CheckResult:
    load_id: int
    blocking: int = 0
    warnings: int = 0
    by_check: dict[str, int] = field(default_factory=dict)
    issues: list[dict] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return self.blocking == 0


# ---------------------------------------------------------------- definitions
def parse(raw: dict, submission_type: str, columns: set[str] | dict[str, set[str]]) -> CheckSet:
    """`columns`: the staging columns per sheet (or one set used for every sheet)."""
    where = f"{submission_type} input_checks.yaml"
    if isinstance(raw, dict) and "sheets" in raw and not set(raw) - {"sheets"}:
        blocks = raw["sheets"]
    elif isinstance(raw, dict) and not set(raw) - {"sheet", "checks"} and raw.get("checks"):
        blocks = [raw]
    else:
        raise CheckDefinitionError(f"{where}: needs 'sheet' and a non-empty 'checks' list (or 'sheets')")
    cols_of = (lambda name: columns) if isinstance(columns, set) else columns.get
    seen, sheets = set(), []
    for block in blocks:
        if not isinstance(block, dict) or set(block) - {"sheet", "checks"} or not block.get("checks"):
            raise CheckDefinitionError(f"{where}: each sheet needs 'sheet' and a non-empty 'checks' list")
        sheet = str(block["sheet"])
        if cols_of(sheet) is None:
            raise CheckDefinitionError(f"{where}: sheet {sheet!r} is not in inputs.yaml")
        sheets.append(
            SheetChecks(
                sheet, tuple(_parse_checks(block["checks"], f"{where} [{sheet}]", cols_of(sheet), cols_of, seen))
            )
        )
    return CheckSet(submission_type, tuple(sheets))


def _parse_checks(raw_checks, where: str, columns: set[str], cols_of, seen: set) -> list[Check]:
    checks = []
    for i, c in enumerate(raw_checks):
        label = f"{where}: check #{i + 1}"
        if not isinstance(c, dict) or not {"id", "check", "severity", "message"} <= set(c):
            raise CheckDefinitionError(f"{label}: needs id, check, severity, message")
        if not re.match(r"^[A-Z][A-Z0-9_]{2,40}$", str(c["id"])) or c["id"] in seen:
            raise CheckDefinitionError(f"{label}: id {c['id']!r} must be A-Z, 0-9, _ and unique")
        if c["check"] not in CHECKS:
            raise CheckDefinitionError(f"{label}: unknown check {c['check']!r} (one of {sorted(CHECKS)})")
        if c["severity"] not in SEVERITY:
            raise CheckDefinitionError(f"{label}: severity must be block or warn")
        cols = c.get("columns", [])
        cols = sorted(columns) if cols == "all" else list(cols)
        named = [*cols, *(c[k] for k in ("pin", "state", "other", "by", "code") if k in c), *(c.get("where") or {})]
        unknown = [n for n in named if n not in columns]
        if unknown:
            raise CheckDefinitionError(f"{label} ({c['id']}): not columns of the sheet: {unknown}")
        if c["check"] not in ("pin_state",) and not cols:
            raise CheckDefinitionError(f"{label} ({c['id']}): needs columns")
        if c["check"] == "in_sheet":
            other = cols_of(str(c.get("other_sheet")))
            if other is None or c.get("other_column") not in other:
                raise CheckDefinitionError(f"{label} ({c['id']}): other_sheet / other_column not found")
        options = {k: v for k, v in c.items() if k not in ("id", "check", "columns", "severity", "message")}
        if c["check"] == "pattern":
            re.compile(options.get("pattern", ""))
        seen.add(c["id"])
        checks.append(Check(str(c["id"]), c["check"], tuple(cols), c["severity"], str(c["message"]), options))
    return checks


@cache
def load_checks(submission_type: str) -> CheckSet | None:
    """The type's checks, or None if it has no input_checks.yaml."""
    path = type_file(submission_type, "input_checks.yaml")
    if not path.is_file():
        return None
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    columns = {s.sheet: set(s.columns) for s in load_spec(submission_type).sheets}
    return parse(raw, submission_type.upper(), columns)


# ---------------------------------------------------------------- values
def date_problem(value: str) -> str | None:
    if as_date(value) is not None:
        return None
    if re.fullmatch(r"\d{7}", value) and as_date("0" + value) is not None:
        return f"{value} has 7 digits: the leading zero was lost (format the column as text, DDMMYYYY)"
    return f"{value} is not a date in DDMMYYYY format"


def _years_between(earlier: date, later: date) -> int:
    return later.year - earlier.year - ((later.month, later.day) < (earlier.month, earlier.day))


# ---------------------------------------------------------------- the checks
class _LabelLookup(dict):
    """label -> canonical value, looked up ignoring case and outer spaces (labels from ref_code_translation)."""

    def __init__(self, labels: dict[str, str], normalise):
        super().__init__(labels)
        self.normalise = normalise

    def __contains__(self, key):
        return super().__contains__(self.normalise(key))

    def __getitem__(self, key):
        return super().__getitem__(self.normalise(key))


def _key(c: Check):
    """How a check compares values: as they are, or (match: alnum) letters and digits only, upper case."""
    if c.options.get("match") == "alnum":
        return lambda v: None if v is None else (re.sub(r"[^0-9A-Za-z]", "", str(v)).upper() or None)
    return lambda v: None if v is None else str(v).strip()


class _Run:
    def __init__(self, rows: list[dict], as_of: date, source: Engine, app: Engine, submission_type: str, rows_of=None):
        self.rows, self.as_of, self.source, self.app, self.type = rows, as_of, source, app, submission_type
        self.rows_of = rows_of  # sheet name -> that sheet's rows of the same upload (in_sheet)

    def run(self, c: Check):
        """Yield (row number or None, column or None, detail)."""
        yield from getattr(self, c.check)(c)

    def required(self, c):
        for r in self.rows:
            for col in c.columns:
                if r[col] is None:
                    yield r["source_row"], col, "empty"

    def any_of(self, c):
        for r in self.rows:
            if all(r[col] is None for col in c.columns):
                yield r["source_row"], None, f"none of {', '.join(c.columns)}"

    def duplicate(self, c):
        groups = defaultdict(list)
        for r in self.rows:
            groups[tuple(r[col] for col in c.columns)].append(r["source_row"])
        for rows in groups.values():
            for n in rows[1:]:
                yield n, None, f"same as row {rows[0]}"

    def unique_where(self, c):
        groups = defaultdict(list)
        for r in self.rows:
            if all(r[k] == str(v) for k, v in (c.options.get("where") or {}).items()):
                groups[tuple(r[col] for col in c.columns)].append(r["source_row"])
        for key, rows in groups.items():
            for n in rows[1:]:
                yield n, c.columns[0], f"{key[0]} also on row {rows[0]}"

    def consistent(self, c):
        groups, key = defaultdict(list), _key(c)
        for r in self.rows:
            if r[c.options["by"]] is not None:
                groups[key(r[c.options["by"]])].append(r)
        for key, rows in groups.items():
            first = rows[0]
            for r in rows[1:]:
                for col in c.columns:
                    if r[col] != first[col]:
                        yield (
                            r["source_row"],
                            col,
                            (
                                f"{r[col] or 'empty'} here, {first[col] or 'empty'} on row "
                                f"{first['source_row']} (used) for {key}"
                            ),
                        )

    def pattern(self, c):
        rx = re.compile(c.options["pattern"])
        for r in self.rows:
            for col in c.columns:
                if r[col] is not None and not rx.search(r[col]):
                    yield r["source_row"], col, r[col]

    def date(self, c):
        for r in self.rows:
            for col in c.columns:
                if r[col] is not None and (problem := date_problem(r[col])):
                    yield r["source_row"], col, problem

    def code(self, c):
        from bureau_core.types import type_definition

        listed = type_definition(self.type).translations[c.options["code_list"]]
        allowed = {t.output_code for t in listed.values() if t.active}
        for r in self.rows:
            for col in c.columns:
                if r[col] is not None and r[col] not in allowed:
                    yield r["source_row"], col, f"{r[col]} (allowed: {', '.join(sorted(allowed))})"

    def pin_state(self, c):
        from bureau_core.types import type_definition
        from canonical.addresses import pin_ranges, pin_state_problem

        if c.options.get("state_is_label"):  # the state column holds a label (e.g. Tamil Nadu)
            from canonical.labels import label_values, normalise

            labels = label_values(self.app, self.type, "state_code")
            to_canonical = _LabelLookup(labels, normalise)
        else:
            to_canonical = {
                t.output_code: value for value, t in type_definition(self.type).translations["state_code"].items()
            }
        with self.app.connect() as conn:
            ranges = pin_ranges(conn)
        for r in self.rows:
            pin, state = r[c.options["pin"]], r[c.options["state"]]
            if (
                pin
                and state
                and state in to_canonical
                and (problem := pin_state_problem(pin, to_canonical[state], ranges))
            ):
                yield r["source_row"], c.options["pin"], f"{pin} / state {state}: {problem}"

    def min_age(self, c):
        for r in self.rows:
            for col in c.columns:
                d = as_date(r[col])
                if d is not None and _years_between(d, self.as_of) < int(c.options["years"]):
                    yield r["source_row"], col, f"{r[col]}: under {c.options['years']} on {self.as_of:%d-%m-%Y}"

    def not_before(self, c):
        other = c.options["other"]
        for r in self.rows:
            for col in c.columns:
                d, o = as_date(r[col]), as_date(r[other])
                if d is not None and o is not None and d < o:
                    yield r["source_row"], col, f"{r[col]} is before {other} {r[other]}"

    def not_after_as_of(self, c):
        for r in self.rows:
            for col in c.columns:
                d = as_date(r[col])
                if d is not None and d > self.as_of:
                    yield r["source_row"], col, f"{r[col]} is after the as-of date {self.as_of:%d-%m-%Y}"

    def in_source(self, c):
        col = c.columns[0]
        key = _key(c)
        with self.source.connect() as conn:
            known = {
                key(v)
                for v in conn.execute(text(c.options["sql"]), {"as_of_date": self.as_of}).scalars()
                if v is not None
            }
        missing = defaultdict(list)
        for r in self.rows:
            if r[col] is not None and key(r[col]) not in known:
                missing[r[col]].append(r["source_row"])
        for value, rows in missing.items():
            for n in rows:
                yield n, col, value

    def in_sheet(self, c):
        col, other = c.columns[0], c.options["other_column"]
        key = _key(c)
        known = {key(r[other]) for r in self.rows_of(c.options["other_sheet"]) if r[other] is not None}
        for r in self.rows:
            if r[col] is not None and key(r[col]) not in known:
                yield r["source_row"], col, f"{r[col]} is not in {c.options['other_sheet']}"

    def label(self, c):
        from canonical.labels import label_values, normalise

        allowed = label_values(self.app, self.type, c.options["code_list"])
        if not allowed:
            if any(r[col] is not None for r in self.rows for col in c.columns):
                yield (
                    None,
                    c.columns[0],
                    (
                        f"automn.ref_code_translation has no labels for {self.type} "
                        f"{c.options['code_list']}: fill its label column"
                    ),
                )
            return
        for r in self.rows:
            for col in c.columns:
                if r[col] is not None and normalise(r[col]) not in allowed:
                    yield r["source_row"], col, f"'{r[col]}' is not a label of {c.options['code_list']}"

    def label_matches_code(self, c):
        from bureau_core.types import type_definition
        from canonical.labels import label_values, normalise

        by_label = label_values(self.app, self.type, c.options["code_list"])
        by_code = defaultdict(set)  # a code can stand for several values (e.g. three Mudra types -> 39)
        for v, t in type_definition(self.type).translations[c.options["code_list"]].items():
            by_code[t.output_code].add(v)
        col, code_col = c.columns[0], c.options["code"]
        for r in self.rows:
            label, code = r[col], r[code_col]
            if label is None or code is None or normalise(label) not in by_label or code not in by_code:
                continue  # missing or unknown values are reported by their own checks
            if by_label[normalise(label)] not in by_code[code]:
                yield r["source_row"], code_col, f"code {code} but label '{label}'"


def run_checks(
    engine: Engine,
    load_id: int,
    submission_type: str,
    as_of_date: date,
    source_engine: Engine | None = None,
    record: bool = True,
    skip_kinds: tuple[str, ...] = (),
) -> CheckResult:
    """Run the type's checks on one loaded workbook. Records the problems (input_issue) and marks the load DQ_FAILED
    if any blocking check fails; record=False only computes (read-only preview). A type without input_checks.yaml
    passes. skip_kinds: check kinds not run (the Uploads page: in_source, the loan system for a date, which only a
    request has; owner 2026-11-12)."""
    result = CheckResult(load_id)
    checks = load_checks(submission_type)
    if checks is None:
        return result
    tables, loaded = staging_tables(load_spec(submission_type)), {}

    def rows_of(sheet: str) -> list[dict]:
        if sheet not in loaded:
            table = tables[sheet]
            with engine.connect() as conn:
                loaded[sheet] = [
                    dict(r._mapping)
                    for r in conn.execute(select(table).where(table.c.load_id == load_id).order_by(table.c.source_row))
                ]
        return loaded[sheet]

    issues = []
    for block in checks.sheets:
        run = _Run(rows_of(block.sheet), as_of_date, source_engine or engine, engine, submission_type, rows_of)
        for c in block.checks:
            if c.check in skip_kinds:
                continue
            found = list(run.run(c))
            result.by_check[c.id] = len(found)
            if not found:
                continue
            if c.severity == "block":
                result.blocking += len(found)
            else:
                result.warnings += len(found)
            for n, col, detail in found[:MAX_ISSUES_PER_CHECK]:
                where = f"row {n}: " if n else ""
                issues.append(
                    {
                        "load_id": load_id,
                        "severity": SEVERITY[c.severity],
                        "code": f"DQ.{c.id}",
                        "sheet": block.sheet,
                        "source_row": n,
                        "column_name": col,
                        "message": f"{where}{c.message} ({detail})"[:1000],
                    }
                )
            if len(found) > MAX_ISSUES_PER_CHECK:
                issues.append(
                    {
                        "load_id": load_id,
                        "severity": SEVERITY[c.severity],
                        "code": f"DQ.{c.id}",
                        "sheet": block.sheet,
                        "source_row": None,
                        "column_name": None,
                        "message": f"{c.message}: {len(found) - MAX_ISSUES_PER_CHECK} more row(s) not listed",
                    }
                )
    result.issues = issues
    if not record:
        return result
    with engine.begin() as conn:
        if issues:
            conn.execute(insert(input_issue), issues)
        if not result.passed:
            conn.execute(
                update(input_load)
                .where(input_load.c.load_id == load_id)
                .values(
                    status="DQ_FAILED",
                    error_message=f"{result.blocking} blocking data-quality problem(s); fix the "
                    "workbook and upload it again",
                )
            )
    return result


def summary(result: CheckResult) -> Counter:
    return Counter({k: v for k, v in result.by_check.items() if v})
