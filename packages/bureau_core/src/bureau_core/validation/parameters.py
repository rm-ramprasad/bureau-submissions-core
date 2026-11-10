"""Rule parameters: tunable values the rules use (cut-off dates, minimum lengths, ID patterns), as data.

Defaults are declared in the rule pack (`parameters:`); an NBFC can override them (table validation_parameter,
Rules & policies page). SQL rules read them as bind parameters (:name), Python checks via the rule context, and
rule messages as {name}. Values fixed by the UCRF spec are not parameters.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from functools import cache

import yaml

from bureau_core.enums import SubmissionType

MAX_REGEX_LENGTH = 300


class ParameterError(ValueError):
    """Invalid value; the message is shown to the user."""


@dataclass(frozen=True)
class Parameter:
    name: str
    type: str  # date / int / regex
    default: object
    label: str
    description: str
    used_by: tuple[str, ...]
    min: int | None = None
    max: int | None = None

    def parse(self, raw) -> object:
        """Typed value from text (form or table), validated."""
        text = str(raw).strip() if raw is not None else ""
        if not text:
            raise ParameterError(f"{self.label}: enter a value")
        if self.type == "date":
            try:
                value = date.fromisoformat(text)
            except ValueError:
                raise ParameterError(f"{self.label}: enter a date as YYYY-MM-DD") from None
            if not date(1900, 1, 1) < value <= date(2100, 1, 1):
                raise ParameterError(f"{self.label}: date out of range")
            return value
        if self.type == "int":
            try:
                value = int(text)
            except ValueError:
                raise ParameterError(f"{self.label}: enter a whole number") from None
            if (self.min is not None and value < self.min) or (self.max is not None and value > self.max):
                raise ParameterError(f"{self.label}: must be between {self.min} and {self.max}")
            return value
        if self.type == "regex":
            if len(text) > MAX_REGEX_LENGTH:
                raise ParameterError(f"{self.label}: at most {MAX_REGEX_LENGTH} characters")
            try:
                re.compile(text)
            except re.error as exc:
                raise ParameterError(f"{self.label}: not a valid regular expression ({exc})") from None
            return text
        raise ParameterError(f"{self.label}: unknown parameter type {self.type}")

    def text(self, value) -> str:
        return value.isoformat() if isinstance(value, date) else str(value)


def parse_parameters(raw: dict) -> dict[str, Parameter]:
    """A rule pack's parameters (its `parameters:` list), defaults parsed and checked."""
    out = {}
    for spec in raw.get("parameters", []):
        p = Parameter(
            name=spec["name"],
            type=spec["type"],
            default=None,
            label=spec["label"],
            description=spec["description"],
            used_by=tuple(spec.get("used_by", [])),
            min=spec.get("min"),
            max=spec.get("max"),
        )
        out[p.name] = Parameter(**{**p.__dict__, "default": p.parse(spec["default"])})
    return out


@cache
def builtin_parameters(submission_type: SubmissionType | str) -> dict[str, Parameter]:
    from bureau_core.validation.rules import pack_paths

    return parse_parameters(yaml.safe_load(pack_paths()[SubmissionType(submission_type)].read_text(encoding="utf-8")))


def defaults(submission_type: SubmissionType | str) -> dict[str, object]:
    return {name: p.default for name, p in builtin_parameters(submission_type).items()}


def render_message(message: str, values: dict[str, object]) -> str:
    """A rule message with its {parameter} placeholders filled in (unknown placeholders are left as they are)."""
    try:
        return message.format(**values) if "{" in message else message
    except KeyError, IndexError, ValueError:
        return message
