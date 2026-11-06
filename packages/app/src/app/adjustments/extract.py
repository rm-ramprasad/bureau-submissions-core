"""The adjustments hook of the snapshot builder (sources.extract.Adjustments), in SQL next to the source views:
freeze the request's active adjustments, resolve what they hit, and wrap each entity's SELECT so excluded loans and
borrowers drop out and overridden values replace the source values (app.adjustments.sql)."""

from __future__ import annotations

from typing import Any

from sqlalchemy import Engine, delete, text

from app.adjustments import sql as adjustment_sql
from app.adjustments.tables import snapshot_adjustment, snapshot_adjustment_effect


class SqlAdjustments:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def prepare(self, snapshot_id: int, sql: dict[str, str], run: dict[str, Any]) -> int:
        """Freeze the snapshot request's active adjustments and resolve their effects. Returns how many were frozen
        (0: nothing to apply)."""
        params = {"snapshot_id": snapshot_id, **run}  # the effects embed the account / holder SELECTs
        with self.engine.begin() as conn:
            frozen = conn.execute(text(adjustment_sql.freeze_sql()), params).rowcount
        if frozen:
            with self.engine.begin() as conn:
                for stmt in adjustment_sql.resolve_sql(sql["account"], sql["account_holder"]):
                    conn.execute(text(stmt), params)
        return int(frozen)

    def final_select(self, entity: str, sql: str, columns: list[str]) -> str:
        return adjustment_sql.final_select(entity, sql, columns)

    def purge(self, snapshot_id: int) -> None:
        with self.engine.begin() as conn:
            for table in (snapshot_adjustment_effect, snapshot_adjustment):
                conn.execute(delete(table).where(table.c.snapshot_id == snapshot_id))
