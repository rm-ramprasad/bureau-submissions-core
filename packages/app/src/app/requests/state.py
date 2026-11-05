"""Row status transitions and the derived batch status."""

from __future__ import annotations

from collections.abc import Iterable

from app.enums import BatchStatus
from app.enums import RequestStatus as S

ALLOWED_TRANSITIONS: dict[S, frozenset[S]] = {
    S.DRAFT: frozenset({S.PENDING, S.CANCELLED}),  # submitted for validation, or abandoned
    S.PENDING: frozenset({S.RUNNING, S.CANCELLED}),
    S.RUNNING: frozenset({S.SUCCEEDED, S.FAILED, S.PENDING}),  # PENDING = released to retry later
    S.FAILED: frozenset({S.PENDING}),  # retry
    S.CANCELLED: frozenset({S.PENDING}),  # re-queue
    S.SUCCEEDED: frozenset(),
}

ACTIVE_STATUSES = frozenset({S.DRAFT, S.PENDING, S.RUNNING})  # hold the duplicate guard (active_key)


class InvalidTransitionError(Exception):
    pass


def check_transition(current: S, target: S) -> None:
    if target not in ALLOWED_TRANSITIONS[current]:
        raise InvalidTransitionError(f"Cannot move request from {current} to {target}")


def derive_batch_status(statuses: Iterable[S | str]) -> BatchStatus:
    """Status of a batch, computed from its rows."""
    rows = [S(s) for s in statuses]
    if not rows:
        raise ValueError("A batch has at least one row")
    if all(s is S.DRAFT for s in rows):
        return BatchStatus.DRAFT
    if any(s in ACTIVE_STATUSES for s in rows):
        return BatchStatus.PENDING if all(s is S.PENDING for s in rows) else BatchStatus.RUNNING
    done = [s for s in rows if s is not S.CANCELLED]
    if not done:
        return BatchStatus.CANCELLED
    if all(s is S.SUCCEEDED for s in done):
        return BatchStatus.SUCCEEDED
    if all(s is S.FAILED for s in done):
        return BatchStatus.FAILED
    return BatchStatus.PARTIAL
