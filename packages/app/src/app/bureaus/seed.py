"""Seed placeholder bureau profiles for the client.

PLACEHOLDER VALUES: member IDs, header tags and file-name formats must be replaced with the
values each bureau issued to each NBFC (docs/ROADMAP.md, "Bureau values per NBFC"). Re-running the seed overwrites
them, so once real values are loaded, maintain them in the table instead of here.
"""

from __future__ import annotations

import logging

from app.bureaus.repository import BureauRepository
from app.enums import BureauCode as B
from app.enums import SubmissionType as T
from bureau_core.bureau import BureauProfile

log = logging.getLogger(__name__)

UCRF_FILE_NAME = "{member_id}-{created:%d%m%Y}-{created:%H%M%S}.txt"
# Commercial 4.3: Commercial-<DL/W1/W2/W3/ME...>-<date reported>-<internal reference>
COMMERCIAL_FILE_NAME = "Commercial-{cycle}-{as_of:%d%m%Y}-{member_id}-{created:%H%M%S}.txt"

# bureau -> (display name, member-id prefix, header segment tag)
PLACEHOLDERS = {
    B.EXPERIAN: ("Experian", "EXP", "CONS"),
    B.CIBIL: ("TransUnion CIBIL", "CIB", "TUDF"),
    B.CRIF_HIGHMARK: ("CRIF High Mark", "CRF", "CONS"),
    B.EQUIFAX: ("Equifax", "EQF", "CONS"),
}


def placeholder_profiles(tenant_id: str, tenant_no: int, short_name: str) -> list[BureauProfile]:
    """Consumer and commercial profiles for one tenant; member IDs like CIB0000017 / CIBC000017 (10 chars: TL.01,
    commercial HD.2). Commercial files have no header segment tag."""
    consumer = [
        BureauProfile(
            tenant_id, bureau, T.CONSUMER, name, f"{prefix}{tenant_no:07d}", short_name[:16], tag, UCRF_FILE_NAME
        )
        for bureau, (name, prefix, tag) in PLACEHOLDERS.items()
    ]
    commercial = [
        BureauProfile(
            tenant_id,
            bureau,
            T.COMMERCIAL,
            f"{name} Commercial",
            f"{prefix}C{tenant_no:06d}",
            short_name[:16],
            "",
            COMMERCIAL_FILE_NAME,
        )
        for bureau, (name, prefix, _) in PLACEHOLDERS.items()
    ]
    return consumer + commercial


def seed(repo: BureauRepository, tenant_ids: list[str]) -> int:
    profiles = [
        p for no, tid in enumerate(tenant_ids, 1) for p in placeholder_profiles(tid, no, tid.upper().replace("_", " "))
    ]
    for p in profiles:
        repo.upsert(p)
    return len(profiles)


def seed_missing(repo: BureauRepository, tenant_id: str) -> int:
    """Add the placeholder profiles one NBFC does not have yet (e.g. a new submission type); existing profiles,
    real member IDs included, are never changed. Runs with every migrate (admin function), for the deployment's NBFC."""
    have = {(p.bureau_code, p.submission_type) for p in repo.list(tenant_id, active_only=False)}
    missing = [
        p
        for p in placeholder_profiles(tenant_id, 1, tenant_id.upper().replace("_", " "))
        if (p.bureau_code, p.submission_type) not in have
    ]
    for p in missing:
        repo.upsert(p)
    return len(missing)
