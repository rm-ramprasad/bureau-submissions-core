"""Snapshot header lifecycle (canonical.snapshot)."""

from datetime import date

import pytest

from canonical.enums import SnapshotStatus
from canonical.snapshot import SnapshotExistsError, SnapshotNotFoundError, SnapshotRepository
from canonical.testing import TENANT


@pytest.fixture
def repo(engine, clock):
    return SnapshotRepository(engine, clock=clock)


def _create(repo, batch="b1", type_="CONSUMER"):
    return repo.create(TENANT.tenant_id, type_, date(2026, 9, 30), batch, "m1", "worker-1")


def test_lifecycle(repo, clock) -> None:
    sid = _create(repo)
    snap = repo.get(sid)
    assert snap.status is SnapshotStatus.BUILDING and snap.build_owner == "worker-1" and snap.row_counts == {}
    clock.advance()
    repo.mark_extracted(sid, {"c_account": 3})
    repo.mark_validated(sid)
    repo.mark_validated(sid)  # re-validation is allowed
    snap = repo.get(sid)
    assert snap.status is SnapshotStatus.VALIDATED and snap.row_counts == {"c_account": 3}
    assert repo.latest(TENANT.tenant_id, "CONSUMER").snapshot_id == sid
    assert repo.latest(TENANT.tenant_id, "CONSUMER", date(2026, 9, 29)) is None
    assert repo.find_for_batch("b1", TENANT.tenant_id, "CONSUMER").snapshot_id == sid


def test_illegal_transition_is_refused(repo) -> None:
    sid = _create(repo)
    with pytest.raises(ValueError, match="BUILDING; cannot move to VALIDATED"):
        repo.mark_validated(sid)


def test_duplicate_batch_names_the_existing_snapshot(repo) -> None:
    sid = _create(repo)
    with pytest.raises(SnapshotExistsError) as exc:
        _create(repo)
    assert exc.value.snapshot_id == sid
    assert _create(repo, type_="COMMERCIAL") != sid


def test_failed_purged_listed_and_deleted(repo, clock) -> None:
    a, b = _create(repo, "a"), _create(repo, "b")
    repo.mark_failed(a, "boom")
    repo.mark_purged(b)
    assert repo.get(a).error_message == "boom"
    clock.advance(60)
    assert [
        s.snapshot_id
        for s in repo.list_where(
            [SnapshotStatus.FAILED, SnapshotStatus.PURGED], updated_before=clock(), created_before=clock()
        )
    ] == [a, b]
    repo.delete(a)
    with pytest.raises(SnapshotNotFoundError):
        repo.get(a)
