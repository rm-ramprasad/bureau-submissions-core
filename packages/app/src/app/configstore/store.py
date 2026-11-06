"""The store's logic: build, record, list, export and import configuration versions (see the package docstring)."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import asdict, dataclass, replace
from datetime import datetime
from typing import Any

from sqlalchemy import Connection, Engine, Row, func, select
from sqlalchemy.exc import IntegrityError

from app.bureaus import BureauRepository
from app.configstore import config_version
from app.enums import Outcome, SubmissionType
from app.pipeline import enabled_types
from app.validation import custom
from app.validation import settings as rules
from bureau_core.validation.parameters import ParameterError, builtin_parameters
from canonical.db import utcnow
from clientconfig.document import SCHEMA_VERSION, ConfigDocumentError, changes, dumps, settings_only, validate

__all__ = [
    "ConfigDocumentError",
    "Version",
    "current",
    "document",
    "get",
    "import_document",
    "latest",
    "record",
    "record_baseline",
    "versions",
]


@dataclass(frozen=True)
class Version:
    version_no: int
    created_at: datetime
    created_by: str
    action: str
    target: str | None
    reason: str | None
    changes: list[str]


# ---------------------------------------------------------------- the document
def document(engine: Engine, tenant_id: str) -> dict[str, Any]:
    """The NBFC's current settings as a document (schema in clientconfig/schema.json)."""
    types = enabled_types()
    profiles = BureauRepository(engine).list(tenant_id, active_only=False)
    params: dict[str, dict[str, str]] = {}
    rules_by_type: dict[str, list[dict[str, Any]]] = {}
    for st in types:
        defs = builtin_parameters(st)
        over = {k: defs[k].text(v["value"]) for k, v in rules.overrides(engine, tenant_id, st).items()}
        if over:
            params[str(st)] = dict(sorted(over.items()))
        own = sorted(custom.definitions(engine, tenant_id, st).values(), key=lambda d: d.name)
        if own:
            rules_by_type[str(st)] = [asdict(d) for d in own]
    return {
        "schema_version": SCHEMA_VERSION,
        "tenant_id": tenant_id,
        "bureau_profiles": [
            {
                "bureau_code": str(p.bureau_code),
                "submission_type": str(p.submission_type),
                "member_id": p.member_id,
                "member_short_name": p.member_short_name,
                "header_segment_tag": p.header_segment_tag,
                "file_name_pattern": p.file_name_pattern,
                "is_active": bool(p.is_active),
            }
            for p in sorted(profiles, key=lambda p: (str(p.submission_type), str(p.bureau_code)))
        ],
        "rule_policies": {
            rule_id: {"is_enabled": p["is_enabled"], "outcome": p["outcome"]}
            for rule_id, p in sorted(rules.policies(engine, tenant_id).items())
        },
        "rule_parameters": params,
        "custom_rules": rules_by_type,
    }


# ---------------------------------------------------------------- versions
def _last(conn: Connection, tenant_id: str) -> Row[Any, Any] | None:
    t = config_version
    return conn.execute(
        select(t.c.version_no, t.c.document).where(t.c.tenant_id == tenant_id).order_by(t.c.version_no.desc()).limit(1)
    ).first()


def record(
    engine: Engine,
    tenant_id: str,
    user: str,
    action: str,
    target: str | None = None,
    reason: str | None = None,
    clock: Callable[[], datetime] = utcnow,
) -> int:
    """Append the current settings as a new version; returns its number. If nothing differs from the last version,
    no row is added and the last number is returned."""
    doc = document(engine, tenant_id)
    for _attempt in range(5):  # two saves at the same moment: the unique key picks one, the other takes the next number
        with engine.begin() as conn:
            last = _last(conn, tenant_id)
            before = json.loads(last.document) if last else None
            if last and before is not None and dumps(settings_only(before)) == dumps(doc):
                return int(last.version_no)
            number = (int(last.version_no) if last else 0) + 1
            try:
                conn.execute(
                    config_version.insert().values(
                        tenant_id=tenant_id,
                        version_no=number,
                        created_at=clock(),
                        created_by=user[:100],
                        action=action[:50],
                        target=(target or None) and target[:200],
                        reason=(reason or None) and reason[:500],
                        changes=json.dumps(changes(before, doc) if before else []),
                        document=dumps(doc),
                    )
                )
                return number
            except IntegrityError:
                continue
    raise RuntimeError(f"Could not record a configuration version for {tenant_id}")


def record_baseline(engine: Engine, tenant_id: str, user: str = "migrate") -> int | None:
    """Version 1 for an NBFC that has none yet (the settings as they were when versioning started)."""
    if latest(engine, tenant_id) is not None:
        return None
    return record(engine, tenant_id, user, "BASELINE", reason="Settings when configuration versions started")


def latest(engine: Engine, tenant_id: str) -> int | None:
    t = config_version
    with engine.connect() as conn:
        return conn.execute(select(func.max(t.c.version_no)).where(t.c.tenant_id == tenant_id)).scalar()


def versions(engine: Engine, tenant_id: str, limit: int = 200) -> list[Version]:
    t = config_version
    with engine.connect() as conn:
        rows = conn.execute(
            select(t.c.version_no, t.c.created_at, t.c.created_by, t.c.action, t.c.target, t.c.reason, t.c.changes)
            .where(t.c.tenant_id == tenant_id)
            .order_by(t.c.version_no.desc())
            .limit(limit)
        ).all()
    return [
        Version(r.version_no, r.created_at, r.created_by, r.action, r.target, r.reason, json.loads(r.changes or "[]"))
        for r in rows
    ]


def get(engine: Engine, tenant_id: str, version_no: int) -> dict[str, Any] | None:
    """The settings document of one version (None if there is no such version)."""
    t = config_version
    with engine.connect() as conn:
        text = conn.execute(
            select(t.c.document).where(t.c.tenant_id == tenant_id, t.c.version_no == version_no)
        ).scalar()
    return json.loads(text) if text else None


def current(engine: Engine, tenant_id: str) -> dict[str, Any]:
    """The current settings for export, with the version they belong to."""
    return document(engine, tenant_id) | {"version_no": latest(engine, tenant_id), "exported_at": utcnow().isoformat()}


# ---------------------------------------------------------------- import
def _custom_prefix(tenant_id: str) -> str:
    return f"CUSTOM.{tenant_id.upper()}."


def import_document(engine: Engine, tenant_id: str, raw: object, user: str, reason: str) -> int:
    """Replace the NBFC's settings with the document's; returns the new version number.

    Everything is checked before anything is written: the schema, then each value with the same checks the pages use
    (bureau profiles must exist, file-name patterns must work, parameters must parse, custom rules must build,
    policies must name a known rule and outcome). A file exported from another NBFC may be imported: its custom rule
    ids are renamed to this NBFC's.
    """
    doc = validate(raw)
    problems: list[str] = []
    repo = BureauRepository(engine)
    types = {str(t): t for t in enabled_types()}

    # bureau profiles: only existing ones (an import does not add bureaus)
    new_profiles = []
    for p in doc["bureau_profiles"]:
        try:
            cur = repo.get(tenant_id, p["bureau_code"], p["submission_type"])
        except Exception:
            problems.append(f"bureau profile {p['bureau_code']}/{p['submission_type']}: not set up for {tenant_id}")
            continue
        upd = replace(cur, **{k: p[k] for k in ("member_id", "member_short_name", "header_segment_tag",
                                               "file_name_pattern", "is_active")})  # fmt: skip
        if upd.submission_type is SubmissionType.CONSUMER and not upd.header_segment_tag:
            problems.append(f"bureau profile {p['bureau_code']}/CONSUMER: header tag is required")
        try:
            upd.file_name(utcnow(), utcnow().date())
        except Exception as exc:
            problems.append(f"bureau profile {p['bureau_code']}/{p['submission_type']}: {exc}")
        if upd != cur:
            new_profiles.append(upd)

    # custom rules (built first: policies may name them)
    source_prefix = _custom_prefix(doc["tenant_id"])
    defs: dict[SubmissionType, list[custom.Definition]] = {}
    for st_name, items in doc["custom_rules"].items():
        if st_name not in types:
            problems.append(f"custom rules: unknown submission type {st_name}")
            continue
        for item in items:
            d = custom.Definition(**item)
            try:
                custom.build(tenant_id, d)
            except custom.CustomRuleError as exc:
                problems.append(f"custom rule {d.name}: {exc}")
            defs.setdefault(types[st_name], []).append(d)
    custom_ids = {d.rule_id(tenant_id) for ds in defs.values() for d in ds}

    # policies
    known = {e.default.rule_id for st in types.values() for e in rules.catalogue(engine, tenant_id, st)} | custom_ids
    policies: dict[str, dict[str, Any]] = {}
    for rule_id, p in doc["rule_policies"].items():
        rid = (
            _custom_prefix(tenant_id) + rule_id[len(source_prefix) :] if rule_id.startswith(source_prefix) else rule_id
        )
        if rid not in known:
            problems.append(f"rule policy {rule_id}: no such rule")
        if p["outcome"] is not None and p["outcome"] not in {o.value for o in Outcome}:
            problems.append(f"rule policy {rule_id}: unknown outcome {p['outcome']}")
        policies[rid] = p

    # parameters
    for st_name, values in doc["rule_parameters"].items():
        if st_name not in types:
            problems.append(f"rule parameters: unknown submission type {st_name}")
            continue
        defs_p = builtin_parameters(st_name)
        for name, text in values.items():
            if name not in defs_p:
                problems.append(f"rule parameter {st_name}/{name}: no such parameter")
                continue
            try:
                defs_p[name].parse(text)
            except ParameterError as exc:
                problems.append(f"rule parameter {st_name}/{name}: {exc}")

    if problems:
        more = f" (and {len(problems) - 10} more)" if len(problems) > 10 else ""
        raise ConfigDocumentError("The configuration was not imported: " + "; ".join(problems[:10]) + more)

    # apply (each part as the pages do it)
    for prof in new_profiles:
        repo.upsert(prof)
    for st in types.values():
        wanted = {d.rule_id(tenant_id): d for d in defs.get(st, [])}
        for rule_id in custom.definitions(engine, tenant_id, st):
            if rule_id not in wanted:
                custom.delete_rule(engine, tenant_id, rule_id)
        existing = custom.definitions(engine, tenant_id, st)
        for rule_id, d in wanted.items():
            if existing.get(rule_id) != d:
                custom.save(engine, tenant_id, st, d, user, replace_existing=rule_id in existing)
        params = doc["rule_parameters"].get(str(st), {})
        for name in rules.overrides(engine, tenant_id, st):
            if name not in params:
                rules.clear_parameter(engine, tenant_id, st, name)
        for name, text in params.items():
            rules.set_parameter(engine, tenant_id, st, name, text, user)
    for rule_id in rules.policies(engine, tenant_id):
        if rule_id not in policies:
            rules.clear_policy(engine, tenant_id, rule_id)
    for rule_id, p in policies.items():
        rules.set_policy(engine, tenant_id, rule_id, is_enabled=p["is_enabled"], outcome=p["outcome"])
    return record(engine, tenant_id, user, "CONFIG_IMPORTED", target=f"from {doc['tenant_id']}", reason=reason)
