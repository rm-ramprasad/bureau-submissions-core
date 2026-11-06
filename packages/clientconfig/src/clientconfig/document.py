"""The configuration document: one NBFC's settings as one JSON object (export / import, and the body of each version).

The layout is fixed by `schema.json` (JSON Schema, draft 2020-12), so a file can be checked before anything is
applied, by the app or by any JSON Schema tool. This module knows the shape only; the app builds the document from
its tables and applies it (app.configstore).
"""

from __future__ import annotations

import json
from functools import cache
from importlib import resources
from typing import Any

from jsonschema import Draft202012Validator  # type: ignore[import-untyped]

SCHEMA_VERSION = 1
# Fields that describe the document, not the settings: left out when two versions are compared.
META = ("exported_at", "version_no")


class ConfigDocumentError(ValueError):
    """The document does not match the schema; the message lists the problems for the user."""


@cache
def schema() -> dict[str, Any]:
    return dict(json.loads(resources.files("clientconfig").joinpath("schema.json").read_text(encoding="utf-8")))


def validate(document: object) -> dict[str, Any]:
    """The document, if it matches the schema; otherwise ConfigDocumentError naming up to ten problems."""
    validator = Draft202012Validator(schema())
    problems = sorted(validator.iter_errors(document), key=lambda e: list(e.absolute_path))
    if problems:
        lines = [f"{'/'.join(str(p) for p in e.absolute_path) or '(top)'}: {e.message}" for e in problems[:10]]
        more = f" (and {len(problems) - 10} more)" if len(problems) > 10 else ""
        raise ConfigDocumentError("The configuration file is not valid: " + "; ".join(lines) + more)
    assert isinstance(document, dict)
    return document


def settings_only(document: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in document.items() if k not in META}


def dumps(document: dict[str, Any]) -> str:
    """Stable text (sorted keys): equal settings give equal text, so versions compare as strings."""
    return json.dumps(document, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def changes(before: dict[str, Any] | None, after: dict[str, Any]) -> list[str]:
    """What differs between two documents, as paths (e.g. `rule_policies/R1`, `bureau_profiles/CIBIL/CONSUMER`)."""
    old, new = settings_only(before or {}), settings_only(after)
    out: list[str] = []
    for section in sorted(set(old) | set(new)):
        a, b = old.get(section), new.get(section)
        if a == b:
            continue
        if section == "bureau_profiles":
            pa = {f"{p['bureau_code']}/{p['submission_type']}": p for p in a or []}
            pb = {f"{p['bureau_code']}/{p['submission_type']}": p for p in b or []}
            out += [f"{section}/{k}" for k in sorted(set(pa) | set(pb)) if pa.get(k) != pb.get(k)]
        elif isinstance(a, dict) and isinstance(b, dict):
            out += [f"{section}/{k}" for k in sorted(set(a) | set(b)) if a.get(k) != b.get(k)]
        else:
            out.append(section)
    return out
