"""One logging setup for the web app, the worker and the admin commands: one JSON object per line, with the
correlation ID of the HTTP request or job being handled, so every line of one request can be found together (in
Application Insights / CloudWatch, or with grep locally).

    with correlation(request_id="req-42"):   # set by the API per HTTP request and by the worker per job
        log.info("Validated %s rows", n)     # {"time": ..., "level": "INFO", "logger": ..., "message": ...,
                                             #  "correlation_id": "req-42"}
"""

from __future__ import annotations

import json
import logging
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime

_correlation: ContextVar[str | None] = ContextVar("correlation_id", default=None)


def correlation_id() -> str | None:
    return _correlation.get()


@contextmanager
def correlation(value: str | None = None) -> Iterator[str]:
    """Run the block with a correlation ID (a new random one if none is given)."""
    cid = value or uuid.uuid4().hex[:16]
    token = _correlation.set(cid)
    try:
        yield cid
    finally:
        _correlation.reset(token)


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "time": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        cid = _correlation.get()
        if cid:
            entry["correlation_id"] = cid
        if record.exc_info:
            entry["exception"] = self.formatException(record.exc_info)
        return json.dumps(entry, default=str, ensure_ascii=False)


def configure_logging(level: str = "INFO") -> None:
    """JSON lines on stderr. Replaces the root handlers, so calling it again only changes the level."""
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level.upper())
