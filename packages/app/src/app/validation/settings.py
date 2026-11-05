"""The client's validation settings, kept in the database and changed on the Rules page:

- policies (automn.validation_policy): a rule switched on or off, or given another outcome;
- custom rules (automn.validation_rule): the client's own SQL rules (form-built ones: app.validation.custom);
- parameter overrides (automn.validation_parameter): another value for a rule parameter.

The core's built-in rule pack and parameter defaults are the starting point (bureau_core.validation); what is here is
merged over them and handed to the core per run (load_rule_set, load_parameters).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any

from sqlalchemy import Engine, delete, select, update

from app.enums import Outcome, SubmissionType
from app.validation.tables import validation_parameter, validation_policy, validation_rule
from bureau_core.validation.parameters import ParameterError, builtin_parameters, defaults
from bureau_core.validation.rules import Rule, RuleDefinitionError, _check_rule, builtin_pack, builtin_prefixes
from canonical.db import utcnow

__all__ = [
    "CatalogueEntry",
    "ParameterError",
    "RuleDefinitionError",
    "catalogue",
    "clear_parameter",
    "clear_policy",
    "load_parameters",
    "load_rule_set",
    "overrides",
    "policies",
    "save_custom_rule",
    "set_parameter",
    "set_policy",
]


# ---------------------------------------------------------------- custom rules and policy
def save_custom_rule(engine: Engine, tenant_id: str, submission_type: SubmissionType | str, rule: Rule) -> None:
    """Insert or update a custom SQL rule (validated first)."""
    if rule.rule_id.startswith(builtin_prefixes()):
        raise RuleDefinitionError(
            f"{rule.rule_id}: custom rule ids must not start with {' or '.join(builtin_prefixes())}"
        )
    if not rule.sql:
        raise RuleDefinitionError(f"{rule.rule_id}: custom rules are SQL rules")
    _check_rule(rule)
    values = dict(
        tenant_id=tenant_id,
        submission_type=str(SubmissionType(submission_type)),
        entity=rule.entity,
        segment=rule.segment,
        field=rule.field,
        outcome=str(rule.outcome),
        message=rule.message,
        sql_text=rule.sql,
        is_enabled=rule.enabled,
        updated_at=utcnow(),
    )
    with engine.begin() as conn:
        changed = conn.execute(
            update(validation_rule).where(validation_rule.c.rule_id == rule.rule_id).values(**values)
        ).rowcount
        if changed == 0:
            conn.execute(validation_rule.insert().values(rule_id=rule.rule_id, **values))


def set_policy(
    engine: Engine,
    tenant_id: str,
    rule_id: str,
    *,
    is_enabled: bool | None = None,
    outcome: Outcome | str | None = None,
) -> None:
    """Override a rule's enabled flag and/or outcome (None keeps the rule's own value)."""
    values = dict(is_enabled=is_enabled, outcome=str(Outcome(outcome)) if outcome else None, updated_at=utcnow())
    t = validation_policy
    with engine.begin() as conn:
        key = (t.c.tenant_id == tenant_id, t.c.rule_id == rule_id)
        if conn.execute(update(t).where(*key).values(**values)).rowcount == 0:
            conn.execute(t.insert().values(tenant_id=tenant_id, rule_id=rule_id, **values))


def clear_policy(engine: Engine, tenant_id: str, rule_id: str) -> bool:
    """Back to the rule's own settings. True if there was an override."""
    t = validation_policy
    with engine.begin() as conn:
        return conn.execute(delete(t).where(t.c.tenant_id == tenant_id, t.c.rule_id == rule_id)).rowcount > 0


def policies(engine: Engine, tenant_id: str) -> dict[str, dict[str, Any]]:
    """rule_id -> {is_enabled, outcome, updated_at} overrides."""
    t = validation_policy
    with engine.connect() as conn:
        return {
            r.rule_id: dict(is_enabled=r.is_enabled, outcome=r.outcome, updated_at=r.updated_at)
            for r in conn.execute(select(t).where(t.c.tenant_id == tenant_id))
        }


def _own_rules(engine: Engine, tenant_id: str, submission_type: SubmissionType | str) -> list[Rule]:
    """Built-in + custom rules with their own settings (no policy applied)."""
    pack = builtin_pack(submission_type)
    t = validation_rule
    with engine.connect() as conn:
        custom = conn.execute(
            select(t).where(t.c.tenant_id == tenant_id, t.c.submission_type == str(SubmissionType(submission_type)))
        ).all()
    return list(pack.rules) + [
        Rule(
            rule_id=r.rule_id,
            source="CUSTOM",
            entity=r.entity,
            outcome=Outcome(r.outcome),
            message=r.message,
            segment=r.segment,
            field=r.field,
            sql=r.sql_text,
            enabled=bool(r.is_enabled),
            category=pack.custom_category,
        )
        for r in custom
    ]


def _apply(rule: Rule, p: dict[str, Any] | None) -> Rule:
    if p is None:
        return rule
    return replace(
        rule,
        enabled=rule.enabled if p["is_enabled"] is None else bool(p["is_enabled"]),
        outcome=Outcome(p["outcome"]) if p["outcome"] else rule.outcome,
    )


@dataclass(frozen=True)
class CatalogueEntry:
    default: Rule  # the rule's own settings
    effective: Rule  # with the client's policy applied
    policy: dict[str, Any] | None  # the client's override, if any

    @property
    def changed(self) -> bool:
        return (self.default.enabled, self.default.outcome) != (self.effective.enabled, self.effective.outcome)


def catalogue(engine: Engine, tenant_id: str, submission_type: SubmissionType | str) -> list[CatalogueEntry]:
    """Every rule for this type, with its default and effective settings (Rules page)."""
    pol = policies(engine, tenant_id)
    return [
        CatalogueEntry(r, _apply(r, pol.get(r.rule_id)), pol.get(r.rule_id))
        for r in _own_rules(engine, tenant_id, submission_type)
    ]


def load_rule_set(engine: Engine, tenant_id: str, submission_type: SubmissionType | str) -> list[Rule]:
    """Built-in + custom rules with the policy applied. Disabled rules are included (enabled=False)."""
    pol = policies(engine, tenant_id)
    return [_apply(r, pol.get(r.rule_id)) for r in _own_rules(engine, tenant_id, submission_type)]


# ---------------------------------------------------------------- parameter overrides
def overrides(engine: Engine, tenant_id: str, submission_type: SubmissionType | str) -> dict[str, dict[str, Any]]:
    """name -> {value (typed), updated_by, updated_at}; unknown names are ignored."""
    params = builtin_parameters(submission_type)
    t = validation_parameter
    with engine.connect() as conn:
        rows = conn.execute(
            select(t).where(t.c.tenant_id == tenant_id, t.c.submission_type == str(SubmissionType(submission_type)))
        ).all()
    return {
        r.name: dict(value=params[r.name].parse(r.value), updated_by=r.updated_by, updated_at=r.updated_at)
        for r in rows
        if r.name in params
    }


def load_parameters(engine: Engine, tenant_id: str, submission_type: SubmissionType | str) -> dict[str, object]:
    """Effective values: the defaults with the overrides applied."""
    values = defaults(submission_type)
    values.update({k: v["value"] for k, v in overrides(engine, tenant_id, submission_type).items()})
    return values


def set_parameter(
    engine: Engine,
    tenant_id: str,
    submission_type: SubmissionType | str,
    name: str,
    raw: object,
    user: str,
    clock: Callable[[], datetime] = utcnow,
) -> object:
    """Override one parameter. Setting the default removes the override. Returns the typed value."""
    param = builtin_parameters(submission_type).get(name)
    if param is None:
        raise ParameterError(f"Unknown parameter {name!r}")
    value = param.parse(raw)
    sub = str(SubmissionType(submission_type))
    t = validation_parameter
    key = (t.c.tenant_id == tenant_id, t.c.submission_type == sub, t.c.name == name)
    with engine.begin() as conn:
        if value == param.default:
            conn.execute(delete(t).where(*key))
        elif (
            conn.execute(
                update(t).where(*key).values(value=param.text(value), updated_by=user, updated_at=clock())
            ).rowcount
            == 0
        ):
            conn.execute(
                t.insert().values(
                    tenant_id=tenant_id,
                    submission_type=sub,
                    name=name,
                    value=param.text(value),
                    updated_by=user,
                    updated_at=clock(),
                )
            )
    return value


def clear_parameter(engine: Engine, tenant_id: str, submission_type: SubmissionType | str, name: str) -> bool:
    t = validation_parameter
    with engine.begin() as conn:
        return (
            conn.execute(
                delete(t).where(
                    t.c.tenant_id == tenant_id,
                    t.c.name == name,
                    t.c.submission_type == str(SubmissionType(submission_type)),
                )
            ).rowcount
            > 0
        )
