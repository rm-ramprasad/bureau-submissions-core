"""Excel output of a validated snapshot: one row per accepted record (account x borrower), columns from a layout file
(types/<type>/excel_layout.yaml). The consumer layout is the bureaus' Data Submission Form."""

from __future__ import annotations


def excel_layout(submission_type: str):
    """The type's Excel layout (types/<type>/excel_layout.yaml), or None if the type has none."""
    from bureau_core.formats.excel.layout import load_excel_layout
    from bureau_core.types import type_definition

    path = type_definition(submission_type).path("excel_layout.yaml")
    return load_excel_layout(path) if path.is_file() else None
