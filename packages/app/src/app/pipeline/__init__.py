"""Pipelines per submission type and their registry: every registered type (bureau_core.types, one folder with a
type.yaml each) runs the standard views pipeline with the generic file writer (app.pipeline.files), which picks the
file engine from the type's descriptor. No per-type code (step 5)."""

from __future__ import annotations

from sqlalchemy import Engine

from app.enums import SubmissionType
from app.pipeline.base import FileGenerationUnavailable, Services, SubmissionPipeline


def _pipeline() -> SubmissionPipeline:
    from app.pipeline.files import write_files
    from app.pipeline.views import ViewsPipeline

    return ViewsPipeline(write_files)


def get_pipeline(submission_type: SubmissionType | str) -> SubmissionPipeline | None:
    """The type's pipeline, or None for a name that is not a registered type."""
    try:
        SubmissionType(submission_type)
    except ValueError:
        return None
    return _pipeline()


def enabled_types() -> list[SubmissionType]:
    """Submission types the product supports: the registered type folders, in descriptor order."""
    return list(SubmissionType)


def available_types(engine: Engine) -> list[SubmissionType]:
    """Types the client can run: registered, and the type's source views exist (automn.vw_<type>_*)."""
    from sources.views import has_views

    return [t for t in SubmissionType if has_views(engine, t)]


__all__ = [
    "FileGenerationUnavailable",
    "Services",
    "SubmissionPipeline",
    "available_types",
    "enabled_types",
    "get_pipeline",
]
