"""Load and validate the canonical model definition (model.yaml)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import cache
from pathlib import Path

import yaml

MODEL_PATH = Path(__file__).with_name("model.yaml")

COLUMN_TYPES = {"string", "code", "date", "decimal", "integer"}
DEFAULT_CODE_LENGTH = 40  # wide enough to carry an UNMAPPED raw client value through
MAX_CODE_LENGTH = 40  # canonical values must fit ref_code.code and the c_* code columns (VARCHAR(40))
NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")


class ModelDefinitionError(ValueError):
    pass


@dataclass(frozen=True)
class Code:
    code: str
    description: str
    active: bool = True
    pin_min: int | None = None
    pin_max: int | None = None


@dataclass(frozen=True)
class CodeList:
    name: str
    description: str
    codes: tuple[Code, ...]

    def values(self, active_only: bool = True) -> set[str]:
        return {c.code for c in self.codes if c.active or not active_only}


@dataclass(frozen=True)
class ColumnDef:
    name: str
    type: str
    length: int | None = None
    precision: int = 20
    scale: int = 2
    code_list: str | None = None
    description: str | None = None


@dataclass(frozen=True)
class EntityDef:
    name: str
    table: str
    description: str
    columns: tuple[ColumnDef, ...]
    indexes: tuple[tuple[str, ...], ...]
    account_key: str | None = None  # column holding the record's account key (keys: in the model), if any
    party_key: str | None = None  # column holding the record's party key, if any

    def column(self, name: str) -> ColumnDef:
        return next(c for c in self.columns if c.name == name)

    def key_exprs(self, alias: str = "x") -> tuple[str, str]:
        """SQL for (account_key, party_key) of a row: findings point at records through these."""
        return (
            f"{alias}.{self.account_key}" if self.account_key else "NULL",
            f"{alias}.{self.party_key}" if self.party_key else "NULL",
        )


@dataclass(frozen=True)
class CanonicalModel:
    version: str
    code_lists: dict[str, CodeList]
    entities: dict[str, EntityDef]


def _text(value, where: str) -> str:
    text = str(value)
    if ";" in text:
        raise ModelDefinitionError(f"{where}: no semicolons allowed (migration runner splits on ';')")
    return text


def _parse_code_list(name: str, raw: dict) -> CodeList:
    codes = []
    for code, spec in (raw.get("codes") or {}).items():
        if not isinstance(code, str):
            raise ModelDefinitionError(f"code_lists.{name}: code {code!r} must be quoted as a string")
        if len(code) > MAX_CODE_LENGTH:  # ref_code.code and the c_* code columns
            raise ModelDefinitionError(f"code_lists.{name}: code {code!r} is longer than {MAX_CODE_LENGTH} characters")
        spec = spec if isinstance(spec, dict) else {"description": spec}
        unknown = set(spec) - {"description", "active", "pin_min", "pin_max"}
        if unknown:
            raise ModelDefinitionError(f"code_lists.{name}.{code}: unknown keys {sorted(unknown)}")
        codes.append(
            Code(
                code,
                _text(spec["description"], f"code_lists.{name}.{code}"),
                bool(spec.get("active", True)),
                spec.get("pin_min"),
                spec.get("pin_max"),
            )
        )
    if not codes:
        raise ModelDefinitionError(f"code_lists.{name}: no codes")
    return CodeList(name, _text(raw.get("description", ""), f"code_lists.{name}"), tuple(codes))


def _parse_entity(name: str, raw: dict, code_lists: dict[str, CodeList]) -> EntityDef:
    where = f"entities.{name}"
    if not NAME_RE.match(name) or not NAME_RE.match(raw.get("table", "")):
        raise ModelDefinitionError(f"{where}: invalid entity or table name")
    columns = []
    for col_name, spec in (raw.get("columns") or {}).items():
        cw = f"{where}.{col_name}"
        if not NAME_RE.match(col_name) or col_name in {"row_id", "snapshot_id"}:
            raise ModelDefinitionError(f"{cw}: invalid or reserved column name")
        unknown = set(spec) - {"type", "length", "precision", "scale", "code_list", "description"}
        if unknown:  # required / file field references belong to each submission type
            raise ModelDefinitionError(f"{cw}: unknown keys {sorted(unknown)}")
        col_type = spec.get("type")
        if col_type not in COLUMN_TYPES:
            raise ModelDefinitionError(f"{cw}: type must be one of {sorted(COLUMN_TYPES)}")
        length = spec.get("length")
        if col_type == "string" and not length:
            raise ModelDefinitionError(f"{cw}: string columns need a length")
        if col_type == "code":
            if spec.get("code_list") not in code_lists:
                raise ModelDefinitionError(f"{cw}: unknown code_list {spec.get('code_list')!r}")
            length = length or DEFAULT_CODE_LENGTH
        elif "code_list" in spec:
            raise ModelDefinitionError(f"{cw}: code_list only applies to type code")
        columns.append(
            ColumnDef(
                col_name,
                col_type,
                length,
                spec.get("precision", 20),
                spec.get("scale", 2),
                spec.get("code_list"),
                _text(spec["description"], cw) if spec.get("description") else None,
            )
        )
    if not columns:
        raise ModelDefinitionError(f"{where}: no columns")
    names = {c.name for c in columns}
    indexes = tuple(tuple(ix) for ix in raw.get("indexes", []))
    for ix in indexes:
        if not set(ix) <= names:
            raise ModelDefinitionError(f"{where}: index {list(ix)} uses unknown columns")
    keys = raw.get("keys") or {}
    if set(keys) - {"account", "party"} or not set(keys.values()) <= names:
        raise ModelDefinitionError(f"{where}: keys must map account / party to columns of the entity")
    return EntityDef(
        name,
        raw["table"],
        _text(raw.get("description", ""), where),
        tuple(columns),
        indexes,
        keys.get("account"),
        keys.get("party"),
    )


def parse_model(raw: dict) -> CanonicalModel:
    if not isinstance(raw, dict) or "canonical_version" not in raw:
        raise ModelDefinitionError("canonical_version is required")
    code_lists = {n: _parse_code_list(n, spec) for n, spec in (raw.get("code_lists") or {}).items()}
    entities = {n: _parse_entity(n, spec, code_lists) for n, spec in (raw.get("entities") or {}).items()}
    tables = [e.table for e in entities.values()]
    if len(tables) != len(set(tables)):
        raise ModelDefinitionError("duplicate table names")
    return CanonicalModel(str(raw["canonical_version"]), code_lists, entities)


def load_model(path: Path = MODEL_PATH) -> CanonicalModel:
    return parse_model(yaml.safe_load(path.read_text(encoding="utf-8")))


@cache
def canonical_model() -> CanonicalModel:
    """The packaged model, loaded once."""
    return load_model()
