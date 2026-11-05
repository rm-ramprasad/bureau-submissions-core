"""Workspace-wide test options."""

import os

import pytest


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Workbook tests need the local input templates (gitignored): skipped unless BSC_WORKBOOK_DIR is set."""
    if os.environ.get("BSC_WORKBOOK_DIR"):
        return
    skip = pytest.mark.skip(reason="BSC_WORKBOOK_DIR not set (local input workbooks)")
    for item in items:
        if "workbook" in item.keywords:
            item.add_marker(skip)
