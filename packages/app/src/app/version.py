"""The release this app was built as (e.g. v1.4.0): deploy/build.sh writes app/VERSION; "dev" when run from source."""

from __future__ import annotations

from functools import cache
from pathlib import Path


@cache
def release_version() -> str:
    path = Path(__file__).with_name("VERSION")
    return path.read_text(encoding="utf-8").strip() if path.is_file() else "dev"
