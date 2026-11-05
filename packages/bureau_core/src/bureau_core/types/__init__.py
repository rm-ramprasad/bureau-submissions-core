"""Submission types: one self-contained folder each (bureau_core/types/<type>/), nothing shared between them.

  codes.yaml          canonical value -> this type's file code, per code list (+ named groups of values)
  requirements.yaml   canonical columns this type needs (generated REQUIRED rules, mapping coverage)
  rules.yaml          validation rule pack (rules, categories, record decision)
  layout.yaml         file layout, read by a generic file engine (bureau_core/formats/)
  excel_layout.yaml   the Excel (review / Data Submission Form) layout, if the type has one

A host adds its own side (the app: submissions/types/<type>/ pipeline.py, inputs.yaml, input_checks.yaml).

A type folder is found by its codes.yaml `submission_type:`. Adding a file type (NeSL, MFI, ...) is a new folder plus
its reference rows; no engine changes.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import cache
from pathlib import Path

import yaml

from bureau_core.canonical import canonical_model
from bureau_core.enums import SubmissionType

TYPES_DIR = Path(__file__).parent


class TypeDefinitionError(ValueError):
    pass


@dataclass(frozen=True)
class Translation:
    output_code: str
    active: bool = True


@dataclass(frozen=True)
class TypeDefinition:
    submission_type: SubmissionType
    folder: Path
    translations: dict[str, dict[str, Translation]]  # code list -> canonical value -> file code
    groups: dict[str, tuple[str, ...]]  # group name -> canonical values
    required: dict[str, tuple[str, ...]]  # entity -> required canonical columns

    def path(self, name: str) -> Path:
        return self.folder / name

    def is_required(self, entity: str, column: str) -> bool:
        return column in self.required.get(entity, ())


def _read(path: Path) -> dict:
    if not path.exists():
        raise TypeDefinitionError(f"{path}: missing")
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def _load(folder: Path) -> TypeDefinition:
    codes, req = _read(folder / "codes.yaml"), _read(folder / "requirements.yaml")
    st = SubmissionType(codes["submission_type"])
    if SubmissionType(req["submission_type"]) is not st:
        raise TypeDefinitionError(f"{folder.name}: codes.yaml and requirements.yaml name different types")
    model = canonical_model()

    translations = {}
    for list_name, values in (codes.get("translations") or {}).items():
        code_list = model.code_lists.get(list_name)
        if code_list is None:
            raise TypeDefinitionError(f"{folder.name}/codes.yaml: unknown code list {list_name!r}")
        known = code_list.values(active_only=False)
        out = {}
        for value, spec in values.items():
            if value not in known:
                raise TypeDefinitionError(f"{folder.name}/codes.yaml: {list_name}.{value} is not a canonical value")
            spec = spec if isinstance(spec, dict) else {"code": spec}
            if not isinstance(spec.get("code"), str):
                raise TypeDefinitionError(f"{folder.name}/codes.yaml: {list_name}.{value}: quote the code")
            out[value] = Translation(spec["code"], bool(spec.get("active", True)))
        translations[list_name] = out

    all_values = {v for cl in model.code_lists.values() for v in cl.values(active_only=False)}
    groups = {}
    for name, values in (codes.get("groups") or {}).items():
        unknown = set(values) - all_values
        if unknown:
            raise TypeDefinitionError(f"{folder.name}/codes.yaml: group {name}: unknown values {sorted(unknown)}")
        groups[name] = tuple(values)

    required = {}
    for entity, cols in (req.get("required") or {}).items():
        edef = model.entities.get(entity)
        if edef is None or not set(cols) <= {c.name for c in edef.columns}:
            raise TypeDefinitionError(f"{folder.name}/requirements.yaml: {entity}: unknown entity or columns")
        required[entity] = tuple(cols)
    return TypeDefinition(st, folder, translations, groups, required)


@cache
def type_definitions() -> dict[SubmissionType, TypeDefinition]:
    found = {}
    for folder in sorted(p.parent for p in TYPES_DIR.glob("*/codes.yaml")):
        d = _load(folder)
        if d.submission_type in found:
            raise TypeDefinitionError(
                f"Two folders for {d.submission_type}: {found[d.submission_type].folder.name}, {folder.name}"
            )
        found[d.submission_type] = d
    return found


def type_definition(submission_type: SubmissionType | str) -> TypeDefinition:
    d = type_definitions().get(SubmissionType(submission_type))
    if d is None:
        raise TypeDefinitionError(f"No submission type folder for {submission_type} (bureau_core/types/)")
    return d
