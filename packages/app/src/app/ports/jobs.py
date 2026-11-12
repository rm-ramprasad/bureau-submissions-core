"""Port: start the work for PENDING requests (the queue dispatchers are in the shells).

On Azure each PENDING row becomes one message on a Storage Queue:
  validate-jobs   {"request_id": 123, "run_mode": "VALIDATE"}   -> validate_worker function
  generate-jobs   {"request_id": 124, "run_mode": "GENERATE"}   -> generate_worker function
The queue trigger retries a failed run (host.json maxDequeueCount) and then moves the message to <queue>-poison.

Locally (and in tests) nothing is sent: the polling worker (app.worker) claims PENDING rows itself.
Dispatch always happens after the row is committed; a lost dispatch is repaired by the recovery job, which
re-dispatches PENDING rows that have waited too long. A duplicate message is harmless: the worker's atomic claim on
the row lets only one run through.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterable
from typing import Protocol

from app.requests import RequestRepository, SubmissionRequest

log = logging.getLogger(__name__)


class Dispatcher(Protocol):
    def dispatch(self, requests: Iterable[SubmissionRequest]) -> None:
        """Start the work for these (PENDING) rows."""

    def dispatch_later(self, requests: Iterable[SubmissionRequest], delay_seconds: int) -> None:
        """Start the work for these (PENDING) rows after a delay."""


class NoDispatch:
    """Local development and tests: the polling worker picks PENDING rows up."""

    def dispatch(self, requests: Iterable[SubmissionRequest]) -> None:
        return None

    def dispatch_later(self, requests: Iterable[SubmissionRequest], delay_seconds: int) -> None:
        return None


def job_message(req: SubmissionRequest) -> str:
    return json.dumps({"request_id": req.request_id, "run_mode": str(req.run_mode)})


def upload_message(load_id: int) -> str:
    """An Uploads-page upload to process in the background (sent on the validate queue: no new queue needed)."""
    return json.dumps({"upload_id": load_id})


def upload_id_from_message(body: str) -> int | None:
    """The upload a message is for, or None when it is a request message."""
    try:
        value = json.loads(body).get("upload_id")
    except ValueError, TypeError, AttributeError:
        return None
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else None


def request_id_from_message(body: str) -> int:
    """A job message -> request_id."""
    try:
        request_id = json.loads(body)["request_id"]
    except (ValueError, TypeError, KeyError) as exc:
        raise ValueError(f"Not a job message: {body[:200]!r}") from exc
    if not isinstance(request_id, int) or isinstance(request_id, bool) or request_id < 1:
        raise ValueError(f"Not a job message: {body[:200]!r}")
    return request_id


def dispatch_batch(dispatcher: Dispatcher, requests: RequestRepository, batch_id: str) -> None:
    """Dispatch the PENDING rows of a batch (after create / submit)."""
    dispatcher.dispatch(r for r in requests.list_batch(batch_id) if r.status.value == "PENDING")
