"""Layer 2 codes, stored in the database as their string values."""

from __future__ import annotations

from enum import StrEnum


class SnapshotStatus(StrEnum):
    """Lifecycle of one canonical extract snapshot."""

    BUILDING = "BUILDING"  # extract (and validation) in progress, owned by one worker
    EXTRACTED = "EXTRACTED"  # canonical rows loaded, not yet validated
    VALIDATED = "VALIDATED"  # findings and record outcomes written; ready to format
    FAILED = "FAILED"
    PURGED = "PURGED"  # rows removed by retention; metadata kept


class ExtractStrategy(StrEnum):
    """How the client's source schema is copied into the canonical snapshot."""

    STREAM = "stream"  # source on a separate server/replica: stream rows across
    INSERT_SELECT = "insert_select"  # source in the same database: INSERT ... SELECT
