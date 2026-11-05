"""Source data from SQL views in the database: the joins and conversions live in the views, so a change is an ALTER
VIEW in the database, with no redeployment (user decision, 2026-10-30). Scripts: canonical/migrations/<dialect>/views.

One view per canonical entity, named automn.vw_<type>_<entity> (e.g. automn.vw_consumer_account). A view:
  - returns the entity's canonical columns by name (bureau_core model) with canonical values (labels turned into
    values by joining automn.ref_code_translation); a column it does not return is NULL;
  - also returns `load_id` (the uploaded workbook), which the app filters on, and, for the account view (and any other
    view joined to the source view by date), `as_of_date`: filtered on the request's date when returned. Views that
    only read the upload (borrowers, IDs, phones, ...) leave it out, so they return the upload as is.
account, party and account_holder are required; a type is offered when they exist. Each snapshot records a
fingerprint of the view definitions it read (extract_snapshot.mapping_version), so a file can be traced to its logic.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import date
from typing import Any

from sqlalchemy import Connection, Engine, text

from bureau_core.canonical import canonical_model
from canonical.db import SCHEMA
from canonical.schema import ENTITY_TABLES, MODEL

REQUIRED_ENTITIES = ("party", "account", "account_holder")
DATED = ("account",)  # must return as_of_date; other views may (then filtered on it) or not (upload only)
PARAMS = ("as_of_date", "load_id")


class ViewError(ValueError):
    pass


def uses_param(sql: str, name: str) -> bool:
    return re.search(rf":{name}\b", sql) is not None


def bind_params(sql: str, values: dict[str, Any]) -> dict[str, Any]:
    """The run's parameter values that this SQL uses (text() rejects values for parameters it does not have)."""
    return {name: value for name, value in values.items() if uses_param(sql, name)}


@dataclass(frozen=True)
class CompiledViews:
    sql: dict[str, str]  # canonical entity -> SELECT over its view (canonical column order)
    version: str  # fingerprint of the view definitions, recorded on each snapshot

    def uses(self, name: str) -> bool:
        """True if any entity's SQL uses the parameter :name."""
        return any(uses_param(sql, name) for sql in self.sql.values())


def view_name(submission_type: str, entity: str) -> str:
    return f"vw_{str(submission_type).lower()}_{entity}"


def _views(conn: Connection, prefix: str) -> dict[str, str]:
    """view name -> definition, for the automn views whose name starts with prefix."""
    like = prefix.replace("_", r"\_") + "%"
    dialect = conn.dialect.name
    if dialect == "sqlite":
        sql = f"SELECT name, sql FROM {SCHEMA}.sqlite_master WHERE type = 'view' AND name LIKE :p ESCAPE '\\'"  # noqa: S608 (schema, view and column names come from code, values are bound)
        rows = conn.execute(text(sql), {"p": like}).all()
    elif dialect == "mssql":
        sql = (
            "SELECT v.name, OBJECT_DEFINITION(v.object_id) FROM sys.views v "
            "WHERE SCHEMA_NAME(v.schema_id) = :s AND v.name LIKE :p ESCAPE '\\'"
        )
        rows = conn.execute(text(sql), {"s": SCHEMA, "p": like}).all()
    elif dialect == "postgresql":
        sql = "SELECT viewname, definition FROM pg_views WHERE schemaname = :s AND viewname LIKE :p ESCAPE '\\'"
        rows = conn.execute(text(sql), {"s": SCHEMA, "p": like}).all()
    else:
        raise ViewError(f"source views are not supported on {dialect}")
    return {name.lower(): definition or "" for name, definition in rows}


def _columns(conn: Connection, view: str) -> set[str]:
    if conn.dialect.name == "sqlite":
        return {r[1].lower() for r in conn.execute(text(f"PRAGMA {SCHEMA}.table_info({view})"))}
    sql = "SELECT column_name FROM information_schema.columns WHERE table_schema = :s AND table_name = :v"
    return {r[0].lower() for r in conn.execute(text(sql), {"s": SCHEMA, "v": view})}


def has_views(engine: Engine, submission_type: str) -> bool:
    """True if the type's required views exist (it can run from views)."""
    with engine.connect() as conn:
        found = _views(conn, f"vw_{str(submission_type).lower()}_")
    return all(view_name(submission_type, e) in found for e in REQUIRED_ENTITIES)


def compile_views(engine: Engine, submission_type: str) -> CompiledViews:
    """The per-entity SELECTs over the type's views, filtered on :load_id and :as_of_date."""
    with engine.connect() as conn:
        found = _views(conn, f"vw_{str(submission_type).lower()}_")
        missing = [
            view_name(submission_type, e) for e in REQUIRED_ENTITIES if view_name(submission_type, e) not in found
        ]
        if missing:
            raise ViewError(
                f"Required views missing in schema {SCHEMA}: {', '.join(missing)} (scripts: canonical/migrations/)"
            )
        sql, fingerprint = {}, []
        for entity, definition in MODEL.entities.items():
            view = view_name(submission_type, entity)
            if view not in found:
                continue
            have = _columns(conn, view)
            if "load_id" not in have:
                raise ViewError(f"View {SCHEMA}.{view} must return load_id (the app filters on it)")
            if entity in DATED and "as_of_date" not in have:
                raise ViewError(f"View {SCHEMA}.{view} must return as_of_date (the app filters on it)")
            table = ENTITY_TABLES[
                entity
            ]  # a missing column is a typed NULL: Postgres will not insert text NULL in a date
            cols = [
                f"v.{c.name}"
                if c.name.lower() in have
                else f"CAST(NULL AS {table.c[c.name].type.compile(dialect=conn.dialect)}) AS {c.name}"
                for c in definition.columns
            ]
            where = "v.load_id = :load_id" + (" AND v.as_of_date = :as_of_date" if "as_of_date" in have else "")
            sql[entity] = f"SELECT {', '.join(cols)}\nFROM {SCHEMA}.{view} v\nWHERE {where}"
            fingerprint.append(f"{view}\n{found[view]}")
    version = "views-" + hashlib.sha256("\n".join(fingerprint).encode()).hexdigest()[:16]
    return CompiledViews(sql, version)


def check_views(
    engine: Engine, submission_type: str, as_of_date: date, load_id: int | None
) -> tuple[dict[str, int], list[str], list[str]]:
    """Read-only: rows per view for one upload and date, failing views, and code columns holding values that are not
    canonical (e.g. a label the view did not convert). Returns (rows, errors, warnings)."""
    model = canonical_model()
    rows: dict[str, int] = {}
    errors: list[str] = []
    warnings: list[str] = []
    try:
        compiled = compile_views(engine, submission_type)
    except ViewError as exc:
        return rows, [str(exc)], warnings
    with engine.connect() as conn:
        for entity, sql in compiled.sql.items():
            params = bind_params(sql, {"as_of_date": as_of_date, "load_id": load_id})
            try:
                rows[entity] = conn.execute(text(f"SELECT COUNT(*) FROM (\n{sql}\n) q"), params).scalar_one()  # noqa: S608 (schema, view and column names come from code, values are bound)
            except Exception as exc:  # report every failing view, not just the first
                errors.append(f"{view_name(submission_type, entity)}: {exc}")
                conn.rollback()
                continue
            for col in model.entities[entity].columns:
                if col.type != "code" or not col.code_list:
                    continue
                allowed = model.code_lists[col.code_list].values(active_only=False)
                found = {r[0] for r in conn.execute(text(f"SELECT DISTINCT q.{col.name} FROM (\n{sql}\n) q"), params)}  # noqa: S608 (schema, view and column names come from code, values are bound)
                odd = sorted(str(v) for v in found if v is not None and str(v) not in allowed)
                if odd:
                    warnings.append(
                        f"{view_name(submission_type, entity)}.{col.name}: not {col.code_list} values "
                        f"(label missing in ref_code_translation?): {odd[:15]}"
                    )
    return rows, errors, warnings
