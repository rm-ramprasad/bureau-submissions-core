"""Rule catalogue: built-in packs (YAML, with generated REQUIRED/ENUM rules), tenant custom rules, tenant policy.

A pack (types/<type>/rules.yaml) is everything one submission type declares about validation: its rules, rule ID
prefix, business categories for the findings report, and which record decision (cascade) it uses. The generated
REQUIRED / code rules come from the same folder's requirements.yaml and codes.yaml. Nothing here is specific to a type.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from functools import cache
from pathlib import Path

import yaml

from bureau_core.canonical import canonical_model
from bureau_core.enums import Outcome, SubmissionType
from bureau_core.types import TypeDefinition, type_definition, type_definitions
from bureau_core.validation.cascades import CASCADES

_PACK_KEYS = {
    "pack",
    "submission_type",
    "rule_prefix",
    "segments",
    "categories",
    "custom_category",
    "default_category",
    "cascade",
    "generated",
    "parameters",
    "rules",
}


class RuleDefinitionError(ValueError):
    pass


@dataclass(frozen=True)
class Rule:
    rule_id: str
    source: str  # pack name (e.g. UCRF-3.74) or CUSTOM
    entity: str
    outcome: Outcome
    message: str
    segment: str | None = None
    field: str | None = None
    sql: str | None = None
    python: str | None = None
    cascade: bool = False
    enabled: bool = True
    category: str | None = None  # business category (the pack's categories); built-in rules always have one
    expected: bool = False  # the spec's normal handling, not a data problem: no action, not a "gap"
    generated: bool = False  # a REQUIRED / ENUM rule made from the canonical model

    @property
    def kind(self) -> str:
        return "sql" if self.sql else "python" if self.python else "cascade"


@dataclass(frozen=True)
class Cascade:
    """The record decision a pack uses: a function in validation/cascades.py and its settings from the pack."""

    name: str
    settings: dict = field(default_factory=dict)


@dataclass(frozen=True)
class Pack:
    name: str  # e.g. UCRF-3.74
    submission_type: SubmissionType
    prefix: str  # every built-in rule ID starts with it; custom rule IDs must not
    rules: tuple[Rule, ...]
    categories: dict[str, str]  # key -> label, in display order (custom category last)
    custom_category: str  # category of tenant custom rules
    cascade: Cascade

    def report_hints(self):
        from bureau_core.validation.results import ReportHints

        return ReportHints(
            categories=self.categories,
            custom_category=self.custom_category,
            rule_category={r.rule_id: r.category for r in self.rules},
            expected=frozenset(r.rule_id for r in self.rules if r.expected),
        )


def _check_rule(rule: Rule) -> Rule:
    if rule.entity not in canonical_model().entities:
        raise RuleDefinitionError(f"{rule.rule_id}: unknown entity {rule.entity!r}")
    if sum(map(bool, (rule.sql, rule.python, rule.cascade))) != 1:
        raise RuleDefinitionError(f"{rule.rule_id}: give exactly one of sql, python, cascade")
    if rule.sql and ":snapshot_id" not in rule.sql:
        raise RuleDefinitionError(f"{rule.rule_id}: SQL rules must filter on :snapshot_id")
    if rule.python:
        from bureau_core.validation.python_rules import PYTHON_RULES

        if rule.python not in PYTHON_RULES:
            raise RuleDefinitionError(f"{rule.rule_id}: unknown python rule {rule.python!r}")
    return rule


def _label(column: str) -> str:
    """Readable name of a canonical column for generated messages: birth_date -> Birth date."""
    return column.replace("_", " ").capitalize()


def _generated_rules(pack: str, prefix: str, cfg: dict, segments: dict, tdef: TypeDefinition) -> list[Rule]:
    """REQUIRED rules for the type's required columns (requirements.yaml); code rules for every code column whose
    list the type translates (codes.yaml): a value without an active translation cannot be written to its file.
    Code rules read the snapshot's frozen copy (snapshot_code_translation), which the file writer uses too."""
    model, rules = canonical_model(), []
    for entity, edef in model.entities.items():
        acct, party = edef.key_exprs()
        for col in edef.columns:
            base = dict(source=pack, entity=entity, segment=segments.get(entity), field=col.name, generated=True)
            if tdef.is_required(entity, col.name):
                blank = (
                    f"(x.{col.name} IS NULL OR TRIM(x.{col.name}) = '')"
                    if col.type in ("string", "code")
                    else f"x.{col.name} IS NULL"
                )
                rules.append(
                    Rule(
                        rule_id=f"{prefix}REQUIRED.{entity}.{col.name}",
                        outcome=Outcome(cfg["required_outcome"][entity]),
                        message=f"{_label(col.name)} is required",
                        sql=(
                            f"SELECT x.row_id, {acct} AS account_key, {party} AS party_key, NULL AS value\n"
                            f"FROM automn.{edef.table} x WHERE x.snapshot_id = :snapshot_id AND {blank}"
                        ),
                        **base,
                    )
                )
            if col.code_list and col.code_list in tdef.translations:
                outcome = cfg.get("enum_outcome", {}).get(f"{entity}.{col.name}", cfg["enum_outcome_default"])
                rules.append(
                    Rule(
                        rule_id=f"{prefix}ENUM.{entity}.{col.name}",
                        outcome=Outcome(outcome),
                        message=f"{_label(col.name)} is not a valid code ({col.code_list})",
                        sql=(
                            f"SELECT x.row_id, {acct} AS account_key, {party} AS party_key, x.{col.name} AS value\n"  # noqa: S608  (identifiers from model.yaml)
                            f"FROM automn.{edef.table} x WHERE x.snapshot_id = :snapshot_id AND x.{col.name} IS NOT NULL\n"
                            f"  AND x.{col.name} NOT IN (SELECT t.code FROM automn.snapshot_code_translation t\n"
                            f"    WHERE t.snapshot_id = :snapshot_id AND t.code_list = '{col.code_list}')"
                        ),
                        **base,
                    )
                )
    return rules


def _categories(raw: dict, where: str) -> tuple[dict[str, str], dict[str, str], str]:
    """(key -> label in order, segment code -> key, custom key) from the pack's categories / custom_category."""
    labels, by_segment = {}, {}
    for c in raw.get("categories") or []:
        if set(c) - {"key", "label", "segments"} or not c.get("key") or not c.get("label"):
            raise RuleDefinitionError(f"{where}: each category needs key, label and optional segments")
        labels[c["key"]] = c["label"]
        for seg in c.get("segments", []):
            by_segment[seg] = c["key"]
    custom = raw.get("custom_category") or {}
    if not labels or not custom.get("key") or not custom.get("label") or custom["key"] in labels:
        raise RuleDefinitionError(f"{where}: give categories and a custom_category (key, label) of its own")
    labels[custom["key"]] = custom["label"]
    return labels, by_segment, custom["key"]


def load_pack_file(path: Path) -> Pack:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    unknown = set(raw) - _PACK_KEYS
    if unknown:
        raise RuleDefinitionError(f"{path.name}: unknown keys {sorted(unknown)}")
    tdef = type_definition(raw["submission_type"])
    pack, prefix, segments = raw["pack"], raw.get("rule_prefix") or "", raw.get("segments", {})
    if not prefix.endswith("."):
        raise RuleDefinitionError(f"{path.name}: rule_prefix must end with '.' (e.g. UCRF.)")
    labels, by_segment, custom = _categories(raw, path.name)
    fallback = raw.get("default_category")
    if fallback is not None and fallback not in labels:
        raise RuleDefinitionError(f"{path.name}: default_category {fallback!r} is not a category")
    cascade = raw.get("cascade") or {}
    if cascade.get("name") not in CASCADES:
        raise RuleDefinitionError(
            f"{path.name}: cascade.name must be one of {sorted(CASCADES)} (validation/cascades.py)"
        )

    def category_of(rule_id: str, segment: str | None, explicit: str | None) -> str:
        if explicit is not None:
            if explicit not in labels:
                raise RuleDefinitionError(f"{rule_id}: unknown category {explicit!r} ({', '.join(labels)})")
            return explicit
        token = rule_id[len(prefix) :].split(".")[0]  # <prefix><segment>.<NAME>; generated: their segment
        found = by_segment.get(token) or by_segment.get(segment or "") or fallback
        if found is None:
            raise RuleDefinitionError(f"{rule_id}: no category for segment {token!r} / {segment!r} (pack categories)")
        return found

    rules = [
        replace(r, category=category_of(r.rule_id, r.segment, None))
        for r in _generated_rules(pack, prefix, raw["generated"], segments, tdef)
    ]
    for spec in raw["rules"]:
        unknown = set(spec) - {
            "id",
            "entity",
            "field",
            "outcome",
            "message",
            "sql",
            "python",
            "cascade",
            "category",
            "expected",
        }
        if unknown:
            raise RuleDefinitionError(f"{spec.get('id')}: unknown keys {sorted(unknown)}")
        if not str(spec["id"]).startswith(prefix):
            raise RuleDefinitionError(f"{spec['id']}: built-in rule ids start with {prefix}")
        segment = segments.get(spec["entity"])
        rules.append(
            Rule(
                rule_id=spec["id"],
                source=pack,
                entity=spec["entity"],
                outcome=Outcome(spec["outcome"]),
                message=spec["message"],
                segment=segment,
                field=spec.get("field"),
                sql=spec.get("sql"),
                python=spec.get("python"),
                cascade=bool(spec.get("cascade")),
                category=category_of(spec["id"], segment, spec.get("category")),
                expected=bool(spec.get("expected")),
            )
        )
    ids = [r.rule_id for r in rules]
    duplicates = sorted({i for i in ids if ids.count(i) > 1})
    if duplicates:
        raise RuleDefinitionError(f"{path.name}: duplicate rule ids {duplicates}")
    return Pack(
        name=pack,
        submission_type=SubmissionType(raw["submission_type"]),
        prefix=prefix,
        rules=tuple(_check_rule(r) for r in rules),
        categories=labels,
        custom_category=custom,
        cascade=Cascade(cascade["name"], dict(cascade.get("settings") or {})),
    )


def load_pack(path: Path) -> list[Rule]:
    return list(load_pack_file(path).rules)


@cache
def pack_paths() -> dict[SubmissionType, Path]:
    """The rule pack of every submission type folder (types/<type>/rules.yaml)."""
    found = {}
    for st, tdef in type_definitions().items():
        path = tdef.path("rules.yaml")
        if not path.exists():
            continue  # a type may be defined before its rules are written
        if SubmissionType(yaml.safe_load(path.read_text(encoding="utf-8"))["submission_type"]) is not st:
            raise RuleDefinitionError(f"{path}: submission_type differs from its folder's codes.yaml")
        found[st] = path
    return found


@cache
def builtin_pack(submission_type: SubmissionType | str) -> Pack:
    path = pack_paths().get(SubmissionType(submission_type))
    if path is None:
        raise RuleDefinitionError(f"No built-in rule pack for {submission_type}")
    return load_pack_file(path)


def builtin_rules(submission_type: SubmissionType | str) -> tuple[Rule, ...]:
    return builtin_pack(submission_type).rules


def builtin_prefixes() -> tuple[str, ...]:
    return tuple(builtin_pack(st).prefix for st in pack_paths())


def report_hints(submission_type: SubmissionType | str):
    """Categories, rule categories and expected rules of the type's pack, for the findings report."""
    return builtin_pack(submission_type).report_hints()
