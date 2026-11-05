"""python -m bureau_core.tudf <form.xlsx> [--bureau CIBIL] [--member-id ...] [--as-of 2026-09-30] [--out file.txt]

Convert a filled-in Data Submission Form into the bureau's TUDF file (standalone: no database). Prints what was
converted and every warning (values left out of the file)."""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

from bureau_core.tudf.convert import ConversionError, convert


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m bureau_core.tudf", description=__doc__)
    ap.add_argument("form", type=Path)
    ap.add_argument("--type", default="CONSUMER", dest="submission_type")
    ap.add_argument("--bureau", default="CIBIL", help="CIBIL, EXPERIAN, CRIF_HIGHMARK or EQUIFAX")
    ap.add_argument("--member-id", help="default: the form's member code column")
    ap.add_argument("--member-name", help="member short name; default: the form's")
    ap.add_argument("--header-tag", help="header segment tag; default per bureau")
    ap.add_argument("--as-of", type=date.fromisoformat, help="default: the latest Date Reported in the form")
    ap.add_argument("--cycle", help="reporting cycle, e.g. ME")
    ap.add_argument("--out", type=Path, help="default: <member id>-<date>-<time>.txt next to the form")
    args = ap.parse_args(argv)
    try:
        result = convert(
            args.form,
            submission_type=args.submission_type,
            bureau=args.bureau,
            member_id=args.member_id,
            member_short_name=args.member_name,
            header_tag=args.header_tag,
            as_of_date=args.as_of,
            cycle=args.cycle,
        )
    except (ConversionError, ValueError) as exc:
        print(f"ERROR {exc}", file=sys.stderr)
        return 1
    out = args.out or args.form.with_name(result.file_name)
    out.write_bytes(result.content)
    s = result.stats
    print(
        f"{result.rows} form rows -> {s.written} records written ({s.duplicates} duplicates left out), "
        f"member {result.member_id}, as of {result.as_of_date:%d-%m-%Y} -> {out}"
    )
    for w in result.warnings:
        print(f"WARNING {w}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
