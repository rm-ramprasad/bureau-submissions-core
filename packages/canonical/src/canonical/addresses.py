"""PIN code rules (spec §10.2, Appendix C; ranges from ref_state_pin)."""

from __future__ import annotations

import re

from sqlalchemy import Connection, select

from canonical.schema import ref_state_pin

FOREIGN = "FOREIGN"  # canonical state_code value


def pin_ranges(conn: Connection) -> dict[str, tuple[int, int]]:
    """PIN prefix range per canonical state from ref_state_pin (the database is the master; model.yaml only
    seeds it)."""
    t = ref_state_pin.c
    return {
        r.state_code: (r.pin_prefix_min, r.pin_prefix_max)
        for r in conn.execute(select(t.state_code, t.pin_prefix_min, t.pin_prefix_max))
    }


def pin_digits(pin: str | None) -> str:
    return re.sub(r"\D", "", pin or "")


def pin_format_problem(pin: str | None, state_code: str | None) -> str | None:
    if state_code == FOREIGN:
        return None  # foreign PINs: not validated beyond length/characters
    d = pin_digits(pin)
    if len(d) != 6:
        return "PIN must have exactly 6 digits"
    if d.endswith("000"):
        return "last three PIN digits are 000"
    return None


def pin_state_problem(pin: str | None, state_code: str | None, ranges: dict[str, tuple[int, int]]) -> str | None:
    """PIN prefix outside the state's range (pin_ranges). Only checked when the PIN format is valid."""
    if state_code not in ranges or pin_format_problem(pin, state_code):
        return None
    lo, hi = ranges[state_code]
    prefix = int(pin_digits(pin)[:2])
    return None if lo <= prefix <= hi else f"PIN prefix {prefix:02d} outside {lo}-{hi} for state {state_code}"
