"""Pipelines per submission type and their registry: each app.types.<type> package with a pipeline module
(`PIPELINE`) is one, for a type the core defines (bureau_core.types)."""

from __future__ import annotations

import importlib
from pathlib import Path

from sqlalchemy import Engine

from app.enums import SubmissionType
from app.pipeline.base import FileGenerationUnavailable, Services, SubmissionPipeline
from bureau_core.types import type_definitions

APP_TYPES = Path(__file__).resolve().parent.parent / "types"  # the app's side of each core type


def _discover() -> dict[SubmissionType, SubmissionPipeline]:
    found, defs = {}, type_definitions()
    for st in SubmissionType:  # enum order: consumer first in lists and schedules
        d = defs.get(st)
        if d and (APP_TYPES / d.folder.name / "pipeline.py").exists():
            found[st] = importlib.import_module(f"app.types.{d.folder.name}.pipeline").PIPELINE
    return found


REGISTRY: dict[SubmissionType, SubmissionPipeline] = _discover()


def get_pipeline(submission_type: SubmissionType | str) -> SubmissionPipeline | None:
    return REGISTRY.get(SubmissionType(submission_type))


def enabled_types() -> list[SubmissionType]:
    """Submission types the product supports (a pipeline exists)."""
    return list(REGISTRY)


def available_types(engine: Engine) -> list[SubmissionType]:
    """Types the client can run: a pipeline exists and the type's source views do (automn.vw_<type>_*)."""
    from sources.views import has_views

    return [t for t in REGISTRY if has_views(engine, t)]


__all__ = [
    "REGISTRY",
    "FileGenerationUnavailable",
    "Services",
    "SubmissionPipeline",
    "available_types",
    "enabled_types",
    "get_pipeline",
]
