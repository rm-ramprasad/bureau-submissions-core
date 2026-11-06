"""Plain JSON for the API: dataclasses, enums, dates, decimals and SQLAlchemy rows."""

from __future__ import annotations

import dataclasses
from datetime import date, datetime, timedelta
from decimal import Decimal
from enum import Enum

from flask.json.provider import DefaultJSONProvider


def to_json(value):
    """Recursively convert to JSON-ready values. Datetimes are UTC in the database: sent as ISO 8601 with 'Z'."""
    if value is None or (isinstance(value, (bool, int, float, str)) and not isinstance(value, Enum)):
        return value
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, datetime):
        return value.isoformat(timespec="seconds") + ("Z" if value.tzinfo is None else "")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, timedelta):
        return int(value.total_seconds())
    if isinstance(value, Decimal):
        return str(value)
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {f.name: to_json(getattr(value, f.name)) for f in dataclasses.fields(value)}
    if isinstance(value, dict):
        return {str(to_json(k)): to_json(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [to_json(v) for v in value]
    if hasattr(value, "_mapping"):  # SQLAlchemy Row
        return to_json(dict(value._mapping))
    return str(value)


class ApiJSONProvider(DefaultJSONProvider):
    sort_keys = False

    def dumps(self, obj, **kwargs):
        return super().dumps(to_json(obj), **kwargs)
