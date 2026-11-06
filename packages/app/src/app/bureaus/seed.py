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
from bureau_core.types import descriptor

log = logging.getLogger(__name__)

UCRF_FILE_NAME = descriptor(T.CONSUMER).default_file_name
COMMERCIAL_FILE_NAME = descriptor(T.COMMERCIAL).default_file_name

# bureau -> (display name, member-id prefix, header segment tag)
PLACEHOLDERS = {
    B.EXPERIAN: ("Experian", "EXP", "CONS"),
    B.CIBIL: ("TransUnion CIBIL", "CIB", "TUDF"),
    B.CRIF_HIGHMARK: ("CRIF High Mark", "CRF", "CONS"),
    B.EQUIFAX: ("Equifax", "EQF", "CONS"),
}


def placeholder_profiles(tenant_id: str, tenant_no: int, short_name: str) -> list[BureauProfile]:
    """One profile per bureau and registered type. Consumer (the first type) keeps member IDs like CIB0000017; every
    other type gets a letter after the prefix, e.g. CIBC000017 for commercial (10 chars: TL.01, commercial HD.2).
    Types without a required header tag get none; names and file-name formats come from the type's descriptor."""
    out = []
    for st in T:
        d = descriptor(st)
        for bureau, (name, prefix, tag) in PLACEHOLDERS.items():
            first = st is T.CONSUMER
            member = f"{prefix}{tenant_no:07d}" if first else f"{prefix}{str(st)[0]}{tenant_no:06d}"
            out.append(
                BureauProfile(
                    tenant_id,
                    bureau,
                    st,
                    name if first else f"{name} {d.label}",
                    member,
                    short_name[:16],
                    tag if d.header_tag_required else "",
                    d.default_file_name,
                )
            )
    return out


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
