import pytest

from app.enums import BatchStatus as B
from app.enums import RequestStatus as S
from app.requests.state import InvalidTransitionError, check_transition, derive_batch_status


@pytest.mark.parametrize(
    "current,target",
    [
        (S.PENDING, S.RUNNING),
        (S.PENDING, S.CANCELLED),
        (S.RUNNING, S.SUCCEEDED),
        (S.RUNNING, S.FAILED),
        (S.RUNNING, S.PENDING),
        (S.FAILED, S.PENDING),
        (S.CANCELLED, S.PENDING),
    ],
)
def test_allowed_transitions(current, target):
    check_transition(current, target)


@pytest.mark.parametrize(
    "current,target",
    [
        (S.PENDING, S.SUCCEEDED),
        (S.RUNNING, S.CANCELLED),
        (S.SUCCEEDED, S.PENDING),
        (S.SUCCEEDED, S.FAILED),
        (S.FAILED, S.SUCCEEDED),
    ],
)
def test_rejected_transitions(current, target):
    with pytest.raises(InvalidTransitionError):
        check_transition(current, target)


@pytest.mark.parametrize(
    "statuses,expected",
    [
        ([S.PENDING, S.PENDING], B.PENDING),
        ([S.PENDING, S.RUNNING], B.RUNNING),
        ([S.PENDING, S.SUCCEEDED], B.RUNNING),
        ([S.SUCCEEDED, S.SUCCEEDED], B.SUCCEEDED),
        ([S.SUCCEEDED, S.FAILED], B.PARTIAL),
        ([S.FAILED, S.FAILED], B.FAILED),
        ([S.SUCCEEDED, S.CANCELLED], B.SUCCEEDED),
        ([S.CANCELLED, S.CANCELLED], B.CANCELLED),
        (["SUCCEEDED", "FAILED"], B.PARTIAL),
    ],
)
def test_derive_batch_status(statuses, expected):
    assert derive_batch_status(statuses) is expected


def test_derive_batch_status_empty():
    with pytest.raises(ValueError):
        derive_batch_status([])
