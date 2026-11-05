"""Audit, users, job messages, request stages (pure rules) and JSON logs."""

import json
import logging
from datetime import date, datetime, timedelta

import pytest

from app import audit
from app.enums import BureauCode as B
from app.enums import RequestStatus as S
from app.enums import RunMode as M
from app.enums import SubmissionType as T
from app.logs import JsonFormatter, configure_logging, correlation, correlation_id
from app.ports.jobs import NoDispatch, dispatch_batch, job_message, request_id_from_message
from app.requests.models import SubmissionRequest
from app.requests.stages import Period, Request, Stage
from app.users import UserError, UserRepository

AS_OF = date(2026, 9, 30)
NOW = datetime(2026, 10, 21, 9, 0)


def test_audit_records_and_lists(engine):
    audit.record(engine, "ops.priya", "RUN_CREATED", tenant_id="nbfc_001", target="b1", bureaus=["CIBIL"])
    audit.record(engine, "scheduler", "RUN_SCHEDULED", tenant_id="nbfc_002", target="b2")
    (latest, first) = audit.recent(engine)
    assert (latest.actor, latest.action) == ("scheduler", "RUN_SCHEDULED")
    assert first.details == '{"bureaus": ["CIBIL"]}'
    assert [e.tenant_id for e in audit.recent(engine, tenant_id="nbfc_001")] == ["nbfc_001"]
    assert [e.target for e in audit.recent(engine, actor="ops.priya", action="RUN_CREATED", target="b1")] == ["b1"]
    assert audit.choices(engine) == (["RUN_CREATED", "RUN_SCHEDULED"], ["ops.priya", "scheduler"])
    assert audit.choices(engine, tenant_id="nbfc_002") == (["RUN_SCHEDULED"], ["scheduler"])


def test_an_audit_failure_never_breaks_the_action(engine, caplog):
    engine.dispose()
    audit.record(None, "x", "Y")  # no engine at all: logged, not raised
    assert "Could not write audit event Y" in caplog.text


def test_users_are_matched_by_email(engine):
    users = UserRepository(engine)
    users.add("asha.k", "Asha K", " Asha.K@Company.IN ")
    assert users.find_by_email("asha.k@company.in").username == "asha.k"
    with pytest.raises(UserError, match="already belongs"):
        users.add("asha2", "Asha 2", "asha.k@company.in")
    with pytest.raises(UserError, match="valid e-mail"):
        users.add("bob", "Bob", "bob-at-company")
    users.add("bob", "Bob", "bob@company.in")
    with pytest.raises(UserError, match="already belongs"):
        users.set_email("bob", "asha.k@company.in")
    users.set_email("bob", "robert@company.in")
    assert users.find_by_email("robert@company.in").username == "bob" and users.find_by_email(None) is None


def test_users_can_be_disabled_and_logins_recorded(engine, clock):
    users = UserRepository(engine, clock=clock)
    users.add("asha.k", "Asha K", "asha@company.in", True)
    users.set_active("asha.k", False)
    users.record_login("asha.k")
    (u,) = users.list()
    assert (u.is_active, u.is_admin, u.last_login_at) == (False, True, clock())
    with pytest.raises(UserError, match="No user"):
        users.set_active("nobody", True)


def test_job_messages_and_dispatch(requests_repo):
    v = requests_repo.create_batch("nbfc_001", AS_OF, M.VALIDATE, [T.CONSUMER])
    (row,) = requests_repo.list_batch(v)
    assert json.loads(job_message(row)) == {"request_id": row.request_id, "run_mode": "VALIDATE"}
    assert request_id_from_message(job_message(row)) == row.request_id
    for bad in ("", "nope", "[]", '{"request_id": "7"}', '{"request_id": 0}', '{"request_id": true}'):
        with pytest.raises(ValueError):
            request_id_from_message(bad)
    assert NoDispatch().dispatch([row]) is None and NoDispatch().dispatch_later([row], 5) is None

    sent = []

    class Recording(NoDispatch):
        def dispatch(self, requests):
            sent.extend(r.request_id for r in requests)

    dispatch_batch(Recording(), requests_repo, v)
    assert sent == [row.request_id]


def row(status, mode=M.VALIDATE, **kw) -> SubmissionRequest:
    base = dict(
        request_id=1, batch_id="b", tenant_id="nbfc_001", run_mode=mode, as_of_date=AS_OF,
        submission_type=T.CONSUMER, bureau_code=None, snapshot_id=1, reporting_cycle=None, requested_by="u",
        requested_at=NOW, status=status, worker_id=None, attempt_count=0, started_at=None, finished_at=None,
        output_file_name=None, output_file_path=None, exceptions_file_path=None, total_records=None,
        accepted_records=None, rejected_records=None, duplicate_records=None, excluded_accounts=None,
        overridden_accounts=None, error_message=None,
    )  # fmt: skip
    return SubmissionRequest(**base | kw)


@pytest.mark.parametrize(
    ("validation", "files", "stage"),
    [
        ([S.DRAFT], [], Stage.AWAITING_EXCEPTIONS),
        ([S.CANCELLED], [], Stage.CANCELLED),
        ([S.PENDING], [], Stage.IN_PROGRESS),
        ([S.RUNNING], [], Stage.IN_PROGRESS),
        ([S.SUCCEEDED], [], Stage.VALIDATED),
        ([S.SUCCEEDED], [S.RUNNING], Stage.IN_PROGRESS),
        ([S.SUCCEEDED], [S.FAILED, S.SUCCEEDED], Stage.COMPLETED),
        ([S.SUCCEEDED], [S.FAILED], Stage.FAILED),
        ([S.SUCCEEDED], [S.CANCELLED], Stage.VALIDATED),
        ([S.FAILED], [], Stage.FAILED),
    ],
)
def test_stage_rules(validation, files, stage):
    req = Request("b", [row(s) for s in validation], [row(s, M.GENERATE, bureau_code=B.CIBIL) for s in files])
    assert req.stage is stage


def test_period_start():
    assert Period.THIS_MONTH.start(NOW) == datetime(2026, 10, 1)
    assert Period.LAST_30.start(NOW) == NOW - timedelta(days=30) and Period.ALL.start(NOW) is None


def test_logs_are_json_lines_with_the_correlation_id():
    fmt = JsonFormatter()
    record = logging.LogRecord("app.worker", logging.INFO, __file__, 1, "Validated %s rows", (12,), None)
    assert correlation_id() is None and "correlation_id" not in json.loads(fmt.format(record))
    with correlation("req-42") as cid:
        entry = json.loads(fmt.format(record))
        assert cid == "req-42" == correlation_id()
    assert entry["message"] == "Validated 12 rows" and entry["level"] == "INFO" and entry["correlation_id"] == "req-42"
    assert correlation_id() is None
    with correlation() as generated:
        assert len(generated) == 16
    try:
        raise ValueError("boom")
    except ValueError:
        import sys

        failed = logging.LogRecord("x", logging.ERROR, __file__, 1, "failed", (), sys.exc_info())
    assert "ValueError: boom" in json.loads(fmt.format(failed))["exception"]


def test_configure_logging_replaces_the_handlers():
    root = logging.getLogger()
    saved = root.handlers[:], root.level
    try:
        configure_logging("debug")
        configure_logging("warning")
        assert len(root.handlers) == 1 and isinstance(root.handlers[0].formatter, JsonFormatter)
        assert root.level == logging.WARNING
    finally:
        root.handlers[:], _ = saved[0], root.setLevel(saved[1])
