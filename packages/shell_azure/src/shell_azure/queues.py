"""Azure Storage queues for job dispatch (the app's Dispatcher port): each PENDING row becomes one message on
validate-jobs or generate-jobs, which the queue-triggered worker functions run."""

from __future__ import annotations

import logging
from collections.abc import Iterable
from typing import Any

from app.enums import RunMode
from app.ports.jobs import Dispatcher, NoDispatch, job_message, upload_message
from app.requests import SubmissionRequest

log = logging.getLogger(__name__)


class QueueJobDispatcher:
    def __init__(self, validate_queue: Any, generate_queue: Any) -> None:
        """Queue clients (azure.storage.queue.QueueClient, base64 message encoding)."""
        self.queues = {RunMode.VALIDATE: validate_queue, RunMode.GENERATE: generate_queue}

    def dispatch(self, requests: Iterable[SubmissionRequest]) -> None:
        self.dispatch_later(requests, 0)

    def dispatch_later(self, requests: Iterable[SubmissionRequest], delay_seconds: int) -> None:
        for req in requests:  # visibility_timeout: the message stays hidden from the trigger for the delay
            self.queues[req.run_mode].send_message(job_message(req), visibility_timeout=delay_seconds or None)
            log.info(
                "Dispatched request %s (%s)%s",
                req.request_id,
                req.run_mode,
                f" in {delay_seconds}s" if delay_seconds else "",
            )

    def dispatch_upload(self, load_id: int) -> None:
        """An Uploads-page upload, processed by the validate worker (app.uploadjobs)."""
        self.queues[RunMode.VALIDATE].send_message(upload_message(load_id))
        log.info("Dispatched upload %s", load_id)


def queue_client(settings: Any, queue_name: str) -> Any:
    """Messages are base64-encoded: the Functions queue trigger expects that by default."""
    from azure.storage.queue import QueueClient, TextBase64EncodePolicy

    from shell_azure.sql import credential

    if settings.storage_connection_string:
        return QueueClient.from_connection_string(
            settings.storage_connection_string, queue_name, message_encode_policy=TextBase64EncodePolicy()
        )
    if not settings.queue_account_url:
        raise RuntimeError("SUBMISSIONS_QUEUE_ACCOUNT_URL is required when SUBMISSIONS_DISPATCH=queue")
    return QueueClient(
        settings.queue_account_url, queue_name, credential=credential(), message_encode_policy=TextBase64EncodePolicy()
    )


def make_dispatcher(settings: Any) -> Dispatcher:
    if settings.dispatch == "none":
        return NoDispatch()
    if settings.dispatch != "queue":
        raise RuntimeError(f"SUBMISSIONS_DISPATCH must be 'queue' or 'none', not {settings.dispatch!r}")
    return QueueJobDispatcher(
        queue_client(settings, settings.validate_queue), queue_client(settings, settings.generate_queue)
    )
