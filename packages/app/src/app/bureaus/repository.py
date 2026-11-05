"""Read and maintain bureau profiles (one per tenant x bureau x submission type)."""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime

from sqlalchemy import Engine, select, true, update

from app.bureaus.tables import bureau_profile as t
from app.enums import BureauCode, SubmissionType
from bureau_core.bureau import BureauProfile
from canonical.db import utcnow


class BureauProfileNotFoundError(LookupError):
    pass


class BureauRepository:
    def __init__(self, engine: Engine, clock: Callable[[], datetime] = utcnow):
        self.engine = engine
        self.clock = clock

    def get(
        self, tenant_id: str, bureau_code: BureauCode | str, submission_type: SubmissionType | str
    ) -> BureauProfile:
        with self.engine.connect() as conn:
            row = conn.execute(
                select(t).where(
                    t.c.tenant_id == tenant_id,
                    t.c.bureau_code == str(BureauCode(bureau_code)),
                    t.c.submission_type == str(SubmissionType(submission_type)),
                )
            ).first()
        if row is None:
            raise BureauProfileNotFoundError(f"No bureau profile for {tenant_id}/{bureau_code}/{submission_type}")
        return BureauProfile.from_row(row)

    def list(
        self, tenant_id: str, submission_type: SubmissionType | str | None = None, active_only: bool = True
    ) -> list[BureauProfile]:
        q = select(t).where(t.c.tenant_id == tenant_id).order_by(t.c.submission_type, t.c.display_name)
        if submission_type is not None:
            q = q.where(t.c.submission_type == str(SubmissionType(submission_type)))
        if active_only:
            q = q.where(t.c.is_active == true())
        with self.engine.connect() as conn:
            return [BureauProfile.from_row(r) for r in conn.execute(q)]

    def upsert(self, profile: BureauProfile) -> None:
        """Insert or update a profile. Validates the file-name pattern first."""
        profile.file_name(self.clock(), date.today())
        values = dict(
            display_name=profile.display_name,
            member_id=profile.member_id,
            member_short_name=profile.member_short_name,
            header_segment_tag=profile.header_segment_tag,
            file_name_pattern=profile.file_name_pattern,
            is_active=profile.is_active,
            updated_at=self.clock(),
        )
        key = (
            (t.c.tenant_id == profile.tenant_id)
            & (t.c.bureau_code == str(profile.bureau_code))
            & (t.c.submission_type == str(profile.submission_type))
        )
        with self.engine.begin() as conn:
            if conn.execute(update(t).where(key).values(**values)).rowcount == 0:
                conn.execute(
                    t.insert().values(
                        tenant_id=profile.tenant_id,
                        bureau_code=str(profile.bureau_code),
                        submission_type=str(profile.submission_type),
                        **values,
                    )
                )
