"""Named value formatters a TUDF layout refers to (`format:` on a field).

A value formatter turns one source value into the field text, or None to leave the field out. A multi formatter
(`tags:` fields) turns one or more source values into several field values, and reports whether text had to be cut.
Everything about *where* a formatter is used and with what limits (lengths, caps, maps, widths) is in the layout;
only the algorithms live here.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

from bureau_core.checks.identifiers import normalise
from bureau_core.checks.phones import valid_numbers
from bureau_core.formats.tudf.encoding import (
    amount,
    ddmmyyyy,
    fit_words,
    printable,
    rate,
    signed_amount,
)

ValueFormatter = Callable[[Any, Any, dict], "str | None"]  # (value, row, args) -> text or None
MultiFormatter = Callable[[list, dict], "tuple[list[str], bool]"]  # (values, args) -> (parts, overflowed)


def _id_number(value, row, args) -> str:
    """ID number by type: spaces removed for `strip_space_types`, normalised for `normalise_types`, else printable."""
    raw = printable(value)
    id_type = getattr(row, args.get("type_field", "id_type"))
    if id_type in args.get("strip_space_types", []):
        return raw.replace(" ", "")
    return normalise(raw) if id_type in args.get("normalise_types", []) else raw


def _phone_list(value, row, args) -> str | None:
    """The usable numbers in a phone field, comma-separated (unusable ones removed); None if none is usable."""
    numbers = valid_numbers(value, getattr(row, args.get("type_field", "phone_type")))
    return ",".join(numbers) if numbers else None


def _pin_code(value, row, args) -> str:
    """Digits only for Indian addresses; for the foreign state code, printable without spaces."""
    if getattr(row, args.get("state_field", "state_code")) == args.get("foreign_state"):
        return printable(value).replace(" ", "")
    return re.sub(r"\D", "", str(value))


def _lookup(value, row, args) -> str | None:
    """Value from the layout's `map`; None (field left out) when the value is not in it."""
    return args["map"].get(str(value))


VALUE_FORMATTERS: dict[str, ValueFormatter] = {
    "raw": lambda v, row, args: str(v),
    "text": lambda v, row, args: printable(v),
    "lower_text": lambda v, row, args: printable(v).lower(),
    "alnum": lambda v, row, args: re.sub(r"[^A-Za-z0-9]", "", str(v)),
    "int": lambda v, row, args: str(int(v)),
    "int3": lambda v, row, args: f"{int(v):03d}",
    "ddmmyyyy": lambda v, row, args: ddmmyyyy(v),
    "month_start": lambda v, row, args: ddmmyyyy(v.replace(day=1)),
    "amount": lambda v, row, args: amount(v),
    "signed_amount": lambda v, row, args: signed_amount(v),
    "rate": lambda v, row, args: rate(v),
    "lookup": _lookup,
    "id_number": _id_number,
    "phone_list": _phone_list,
    "pin_code": _pin_code,
}


def _split_words(values: list, args: dict) -> tuple[list[str], bool]:
    """One text (e.g. a full name) packed into `max_parts` fields of `width` without splitting words."""
    return fit_words(" ".join(str(v) for v in values if v is not None), args["width"], args["max_parts"])


def _fit_lines(values: list, args: dict) -> tuple[list[str], bool]:
    """Several lines (e.g. address lines) kept as they are when each fits `width`; otherwise all words re-packed
    into `max_parts` lines of `width` without splitting words."""
    lines = [printable(v or "") for v in values]
    lines = [ln for ln in lines if ln]
    if any(len(ln) > args["width"] for ln in lines):
        return fit_words(" ".join(lines), args["width"], args["max_parts"])
    return lines, False


MULTI_FORMATTERS: dict[str, MultiFormatter] = {
    "split_words": _split_words,
    "fit_lines": _fit_lines,
}
