"""Submission types: one self-contained folder each (bureau_core/types/<type>/), nothing shared between them.

  codes.yaml          canonical value -> this type's file code, per code list (+ named groups of values)
  requirements.yaml   canonical columns this type needs (generated REQUIRED rules, mapping coverage)
  rules.yaml          validation rule pack (rules, categories, record decision)
  layout.yaml         file layout, read by a generic file engine (bureau_core/formats/)
  excel_layout.yaml   the Excel (review / Data Submission Form) layout, if the type has one
  type.yaml           the descriptor: label, order, engine, page wording, flags (step 5: the type registry)

A host adds its own side (the app: submissions/types/<type>/ pipeline.py, inputs.yaml, input_checks.yaml).

A type folder is found by its type.yaml. The folders are the product's (here) plus any listed in
SUBMISSIONS_TYPE_PATH (folders separated by the OS path separator, each holding type folders): adding a file type
(NeSL, MFI, ...) is a new folder plus its reference rows and source views; no code changes. A type folder may also
hold the host's side (inputs.yaml, input_checks.yaml) when it is not in the product.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Any

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


ENGINES = ("tudf", "delimited")


def type_dirs() -> list[Path]:
    """Folders holding type folders: the product's, then SUBMISSIONS_TYPE_PATH's."""
    extra = [Path(p) for p in os.environ.get("SUBMISSIONS_TYPE_PATH", "").split(os.pathsep) if p.strip()]
    return [TYPES_DIR, *extra]


@dataclass(frozen=True)
class Descriptor:
    """type.yaml: what the app and the pages need to know about a type."""

    submission_type: str
    label: str
    order: int
    engine: str
    file_label: str
    record_noun: str
    header_tag_required: bool
    default_file_name: str

    @classmethod
    def read(cls, path: Path) -> Descriptor:
        raw: dict[str, Any] = _read(path)
        missing = [f for f in cls.__dataclass_fields__ if f not in raw]
        if missing:
            raise TypeDefinitionError(f"{path}: missing {', '.join(missing)}")
        unknown = set(raw) - set(cls.__dataclass_fields__)
        if unknown:
            raise TypeDefinitionError(f"{path}: unknown keys {', '.join(sorted(unknown))}")
        if raw["engine"] not in ENGINES:
            raise TypeDefinitionError(f"{path}: engine {raw['engine']!r} is not one of {', '.join(ENGINES)}")
        return cls(
            submission_type=str(raw["submission_type"]),
            label=str(raw["label"]),
            order=int(raw["order"]),
            engine=str(raw["engine"]),
            file_label=str(raw["file_label"]),
            record_noun=str(raw["record_noun"]),
            header_tag_required=bool(raw["header_tag_required"]),
            default_file_name=str(raw["default_file_name"]),
        )


@cache
def descriptors() -> dict[str, tuple[Descriptor, Path]]:
    """Type name -> (descriptor, folder), in descriptor order. Read without SubmissionType (which checks against it)."""
    found: dict[str, tuple[Descriptor, Path]] = {}
    for base in type_dirs():
        for path in sorted(base.glob("*/type.yaml")):
            d = Descriptor.read(path)
            if d.submission_type in found:
                raise TypeDefinitionError(
                    f"Two folders for {d.submission_type}: {found[d.submission_type][1]}, {path.parent}"
                )
            found[d.submission_type] = (d, path.parent)
    return dict(sorted(found.items(), key=lambda kv: (kv[1][0].order, kv[0])))


def registered_names() -> tuple[str, ...]:
    return tuple(descriptors())


def descriptor(submission_type: SubmissionType | str) -> Descriptor:
    found = descriptors().get(str(submission_type))
    if found is None:
        raise TypeDefinitionError(f"No submission type folder for {submission_type}")
    return found[0]


def reload() -> None:
    """Forget the cached type folders (after SUBMISSIONS_TYPE_PATH changes: tests)."""
    from bureau_core.validation.rules import pack_paths

    descriptors.cache_clear()
    type_definitions.cache_clear()
    pack_paths.cache_clear()


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
    if SubmissionType(req["submission_type"]) is not st or descriptor(st).submission_type != st:
        raise TypeDefinitionError(f"{folder.name}: type.yaml, codes.yaml and requirements.yaml name different types")
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
    """Every registered type's definition, in descriptor order."""
    return {SubmissionType(name): _load(folder) for name, (_d, folder) in descriptors().items()}


def type_definition(submission_type: SubmissionType | str) -> TypeDefinition:
    d = type_definitions().get(SubmissionType(submission_type))
    if d is None:
        raise TypeDefinitionError(f"No submission type folder for {submission_type} (bureau_core/types/)")
    return d
