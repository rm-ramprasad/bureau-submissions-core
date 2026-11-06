"""Admin: rules and policies, custom rules, rule parameters (per NBFC). Routes on the API blueprint (/api/...)."""

from __future__ import annotations

import re

from flask import jsonify

from app import audit
from app.api.auth import admin_only, current_user
from app.api.common import (
    _tenant_required,
    arg,
    body,
    bp,
    fail,
    message,
    selected_tenant,
    svc,
)
from app.api.config import versioned
from app.enums import (
    Outcome,
    SubmissionType,
)
from app.pipeline import enabled_types
from app.validation import custom as custom_rules
from app.validation import settings as rule_parameters
from app.validation import settings as rules
from bureau_core.validation.parameters import ParameterError, builtin_parameters, render_message


# ---------------------------------------------------------------- admin: rules & policies (per NBFC)
def _rule_kind(rule, custom_defs=None) -> str:
    if rule.source == "CUSTOM":
        return "Custom (form)" if custom_defs and rule.rule_id in custom_defs else "Custom SQL"
    if rule.generated:
        return "Generated"
    return {"sql": "SQL", "python": "Python", "cascade": "Record decision"}[rule.kind]


def _rules_type() -> SubmissionType:
    try:
        sub_type = SubmissionType(arg("type") or enabled_types()[0])
    except ValueError:
        fail("Unknown submission type")
    if sub_type not in enabled_types():
        fail(f"{sub_type} is not available yet")
    return sub_type


def _rule_json(rule, values) -> dict:
    return {
        "rule_id": rule.rule_id,
        "source": rule.source,
        "entity": rule.entity,
        "segment": rule.segment,
        "field": rule.field,
        "outcome": rule.outcome,
        "enabled": rule.enabled,
        "kind": rule.kind,
        "message": render_message(rule.message, values),
        "sql": rule.sql,
        "python": rule.python,
    }


@bp.get("/rules")
def rules_page():
    s = svc()
    tenant_id = selected_tenant()
    sub_type = _rules_type()
    custom_defs = custom_rules.definitions(s.engine, tenant_id, sub_type) if tenant_id else {}
    definitions = builtin_parameters(sub_type)
    overridden = rule_parameters.overrides(s.engine, tenant_id, sub_type) if tenant_id else {}
    values = {n: overridden[n]["value"] if n in overridden else p.default for n, p in definitions.items()}
    entries = rules.catalogue(s.engine, tenant_id or "", sub_type)
    return jsonify(
        tenant_id=tenant_id,
        submission_type=sub_type,
        rules=[
            {
                **_rule_json(e.effective, values),
                "kind_label": _rule_kind(e.default, custom_defs),
                "default": {"enabled": e.default.enabled, "outcome": e.default.outcome},
                "changed": e.changed,
                "policy": e.policy,
            }
            for e in entries
        ],
        parameters=[
            {
                "name": p.name,
                "type": p.type,
                "label": p.label,
                "description": p.description,
                "used_by": p.used_by,
                "min": p.min,
                "max": p.max,
                "default": p.text(p.default),
                "value": p.text(values[p.name]),
                "override": overridden.get(p.name),
            }
            for p in definitions.values()
        ],
        custom={rid: {**d.__dict__, "describe": d.describe()} for rid, d in custom_defs.items()},
        checks=custom_rules.CHECKS,
        operators=custom_rules.OPERATORS,
        fields=custom_rules.fields(),
        outcomes=[o.value for o in Outcome],
    )


def _definition(b: dict) -> custom_rules.Definition:
    entity, _, field_name = (b.get("field") or "").partition(".")
    other_entity, _, other = (b.get("other_field") or "").partition(".")
    when_entity, _, when = (b.get("when_field") or "").partition(".")
    if other and other_entity != entity:
        raise custom_rules.CustomRuleError("Compare: pick a field of the same record type")
    if when and when_entity != entity:
        raise custom_rules.CustomRuleError("Condition: pick a field of the same record type")
    return custom_rules.Definition(
        name=(b.get("name") or "").strip().upper(),
        entity=entity,
        field=field_name,
        check=b.get("check") or "",
        outcome=b.get("outcome") or "",
        message=(b.get("message") or "").strip(),
        values=_lines(b.get("values")),
        min=str(b.get("min") or "").strip() or None,
        max=str(b.get("max") or "").strip() or None,
        pattern=(b.get("pattern") or "").strip() or None,
        operator=b.get("operator") or None,
        other_field=other or None,
        when_field=when or None,
        when_values=_lines(b.get("when_values")),
    )


def _lines(raw) -> list[str]:
    """Values as a list, or typed one per line / comma-separated (spaces inside a value are kept)."""
    if isinstance(raw, list):
        return [str(v).strip() for v in raw if str(v).strip()]
    return [v.strip() for v in re.split(r"[\n,]", raw or "") if v.strip()]


@bp.post("/rules/custom/preview")
@admin_only
def preview_custom_rule():
    """Count the findings on the latest validation. Nothing is saved."""
    s = svc()
    tenant_id = _tenant_required("custom rules")
    sub_type = _rules_type()
    try:
        rule = custom_rules.build(tenant_id, _definition(body()))
    except custom_rules.CustomRuleError as exc:
        fail(str(exc))
    snap = s.snapshots.latest(tenant_id, sub_type)
    if snap is None:
        return jsonify(snapshot=None, sql=rule.sql)
    count, examples = custom_rules.preview(
        s.engine, rule, snap.snapshot_id, rule_parameters.load_parameters(s.engine, tenant_id, sub_type)
    )
    return jsonify(snapshot=snap, count=count, examples=examples, sql=rule.sql)


@bp.post("/rules/custom")
@admin_only
def save_custom_rule():
    s, b = svc(), body()
    tenant_id = _tenant_required("custom rules")
    sub_type = _rules_type()
    editing = b.get("editing") or None
    try:
        d = _definition(b)
        if editing and d.rule_id(tenant_id) != editing:
            raise custom_rules.CustomRuleError("The name of an existing rule cannot change; add a new rule instead")
        rule = custom_rules.build(tenant_id, d)
        reason = (b.get("reason") or "").strip()
        if not reason:
            raise custom_rules.CustomRuleError("Give a reason for the rule (it is kept in the audit trail)")
        custom_rules.save(s.engine, tenant_id, sub_type, d, current_user(), replace_existing=bool(editing))
    except custom_rules.CustomRuleError as exc:
        fail(str(exc))
    audit.record(
        s.engine,
        current_user(),
        "CUSTOM_RULE_CHANGED" if editing else "CUSTOM_RULE_ADDED",
        tenant_id=tenant_id,
        target=rule.rule_id,
        reason=reason[:500],
        rule=d.describe(),
        outcome=d.outcome,
        message=d.message,
    )
    versioned(tenant_id, "CUSTOM_RULE_CHANGED" if editing else "CUSTOM_RULE_ADDED", rule.rule_id, reason[:500])
    return message(
        f"{rule.rule_id} {'updated' if editing else 'added'}: {d.describe()}. "
        "Applies to requests validated from now on.",
        rule_id=rule.rule_id,
    )


@bp.post("/rules/custom/<rule_id>/delete")
@admin_only
def delete_custom_rule(rule_id: str):
    s = svc()
    tenant_id = _tenant_required("custom rules")
    d = custom_rules.definitions(s.engine, tenant_id, _rules_type()).get(rule_id)
    if not custom_rules.delete_rule(s.engine, tenant_id, rule_id):
        fail(f"No custom rule {rule_id}", 404)
    rules.clear_policy(s.engine, tenant_id, rule_id)
    audit.record(
        s.engine,
        current_user(),
        "CUSTOM_RULE_DELETED",
        tenant_id=tenant_id,
        target=rule_id,
        rule=d.describe() if d else None,
        reason=(body().get("reason") or "").strip() or None,
    )
    versioned(tenant_id, "CUSTOM_RULE_DELETED", rule_id, (body().get("reason") or "").strip()[:500] or None)
    return message(f"{rule_id} deleted. Earlier findings keep its id.")


@bp.post("/rules/parameters/<name>")
@admin_only
def save_rule_parameter(name: str):
    s, b = svc(), body()
    tenant_id = _tenant_required("parameters")
    sub_type = _rules_type()
    param = builtin_parameters(sub_type).get(name)
    if param is None:
        fail(f"No parameter {name}", 404)
    before = rule_parameters.load_parameters(s.engine, tenant_id, sub_type)[name]
    reason = (b.get("reason") or "").strip()
    try:
        value = param.parse(b.get("value"))
        if value == before:
            return message(f"{param.label}: nothing changed.")
        if not reason:
            raise ParameterError(f"{param.label}: give a reason for the change (kept in the audit trail)")
        rule_parameters.set_parameter(s.engine, tenant_id, sub_type, name, b.get("value"), current_user())
    except ParameterError as exc:
        fail(str(exc))
    audit.record(
        s.engine,
        current_user(),
        "RULE_PARAMETER_CHANGED",
        tenant_id=tenant_id,
        target=name,
        reason=reason[:500],
        before=param.text(before),
        after=param.text(value),
        default=param.text(param.default),
        rules=list(param.used_by),
    )
    versioned(tenant_id, "RULE_PARAMETER_CHANGED", f"{sub_type}/{name}", reason[:500])
    return message(f"{param.label} = {param.text(value)} for {tenant_id}. Applies to requests validated from now on.")


@bp.post("/rules/parameters/<name>/reset")
@admin_only
def reset_rule_parameter(name: str):
    s = svc()
    tenant_id = _tenant_required("parameters")
    sub_type = _rules_type()
    param = builtin_parameters(sub_type).get(name)
    if param is None:
        fail(f"No parameter {name}", 404)
    before = rule_parameters.load_parameters(s.engine, tenant_id, sub_type)[name]
    if rule_parameters.clear_parameter(s.engine, tenant_id, sub_type, name):
        audit.record(
            s.engine,
            current_user(),
            "RULE_PARAMETER_RESET",
            tenant_id=tenant_id,
            target=name,
            before=param.text(before),
            after=param.text(param.default),
        )
    versioned(tenant_id, "RULE_PARAMETER_RESET", f"{sub_type}/{name}")
    return message(f"{param.label}: back to the default ({param.text(param.default)}) for {tenant_id}.")


@bp.post("/rules/<rule_id>")
@admin_only
def save_rule_policy(rule_id: str):
    s, b = svc(), body()
    tenant_id = _tenant_required("rule settings")
    sub_type = _rules_type()
    entry = next((e for e in rules.catalogue(s.engine, tenant_id, sub_type) if e.default.rule_id == rule_id), None)
    if entry is None:
        fail(f"No rule {rule_id}", 404)
    enabled = bool(b.get("enabled"))
    try:
        outcome = Outcome(b.get("outcome") or entry.default.outcome.value)
    except ValueError:
        fail(f"Unknown outcome {b.get('outcome')}")
    reason = (b.get("reason") or "").strip()
    if (enabled, outcome) == (entry.effective.enabled, entry.effective.outcome):
        return message(f"{rule_id}: nothing changed.")
    if not reason:
        fail(f"{rule_id}: give a reason for the change (it is kept in the audit trail).")
    if (enabled, outcome) == (entry.default.enabled, entry.default.outcome):
        rules.clear_policy(s.engine, tenant_id, rule_id)  # back to the default: no override row
    else:
        rules.set_policy(
            s.engine,
            tenant_id,
            rule_id,
            is_enabled=None if enabled == entry.default.enabled else enabled,
            outcome=None if outcome == entry.default.outcome else outcome,
        )
    audit.record(
        s.engine,
        current_user(),
        "RULE_POLICY_CHANGED",
        tenant_id=tenant_id,
        target=rule_id,
        reason=reason[:500],
        before={"enabled": entry.effective.enabled, "outcome": str(entry.effective.outcome)},
        after={"enabled": enabled, "outcome": str(outcome)},
        default={"enabled": entry.default.enabled, "outcome": str(entry.default.outcome)},
    )
    versioned(tenant_id, "RULE_POLICY_CHANGED", rule_id, reason[:500])
    return message(
        f"{rule_id}: {'on' if enabled else 'off'}, outcome {outcome.value} for {tenant_id}. "
        "Applies to requests validated from now on."
    )


@bp.post("/rules/<rule_id>/reset")
@admin_only
def reset_rule_policy(rule_id: str):
    s = svc()
    tenant_id = _tenant_required("rule settings")
    before = rules.policies(s.engine, tenant_id).get(rule_id)
    if rules.clear_policy(s.engine, tenant_id, rule_id):
        audit.record(
            s.engine,
            current_user(),
            "RULE_POLICY_RESET",
            tenant_id=tenant_id,
            target=rule_id,
            before={"enabled": (before or {}).get("is_enabled"), "outcome": (before or {}).get("outcome")},  # race-safe
            reason=(body().get("reason") or "").strip()[:500] or None,
        )
    versioned(tenant_id, "RULE_POLICY_RESET", rule_id, (body().get("reason") or "").strip()[:500] or None)
    return message(f"{rule_id}: back to the default for {tenant_id}.")
