"""The schema is configuration (CONVERGENCE-PLAN.md section 3): `automn` is the token, the engine maps it."""

from __future__ import annotations

import pytest
from sqlalchemy import Column, Integer, MetaData, String, Table, event, insert, inspect, select, text

from canonical.db import SCHEMA, configured_schema, make_engine, schema_name


def _table() -> Table:
    return Table(
        "t_schema_probe", MetaData(schema=SCHEMA), Column("id", Integer, primary_key=True), Column("v", String)
    )


def _statements(engine) -> list[str]:
    seen: list[str] = []
    event.listen(engine, "after_cursor_execute", lambda c, cur, stmt, *a: seen.append(stmt))
    return seen


def test_default_is_automn_and_adds_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BSC_SCHEMA", raising=False)
    engine = make_engine("sqlite://")
    assert schema_name(engine) == "automn"
    assert "schema_translate_map" not in engine.get_execution_options()
    seen = _statements(engine)
    with engine.connect() as conn:
        conn.execute(text("SELECT 1 FROM automn.sqlite_master LIMIT 1")).all()
    assert seen == ["SELECT 1 FROM automn.sqlite_master LIMIT 1"]  # byte for byte, as today


@pytest.mark.parametrize("how", ["argument", "environment"])
def test_another_schema_is_used_for_tables_and_hand_written_sql(how: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BSC_SCHEMA", raising=False)
    if how == "environment":
        monkeypatch.setenv("BSC_SCHEMA", "bsc")
        engine = make_engine("sqlite://")
    else:
        engine = make_engine("sqlite://", schema="bsc")
    assert schema_name(engine) == "bsc"
    t = _table()
    t.metadata.create_all(engine)
    seen = _statements(engine)
    with engine.begin() as conn:
        assert schema_name(conn) == "bsc"
        conn.execute(insert(t).values(id=1, v="from the table object"))
        conn.execute(text("INSERT INTO automn.t_schema_probe (id, v) VALUES (2, 'hand-written')"))
        rows = conn.execute(text("SELECT v FROM AUTOMN.t_schema_probe ORDER BY id")).scalars().all()
        assert conn.execute(select(t.c.v).where(t.c.id == 2)).scalar() == "hand-written"
    assert rows == ["from the table object", "hand-written"]
    assert inspect(engine).get_table_names(schema="bsc") == ["t_schema_probe"]
    assert not any("automn" in s.lower() for s in seen), seen


def test_only_the_whole_word_is_replaced() -> None:
    engine = make_engine("sqlite://", schema="bsc")
    seen = _statements(engine)
    with engine.connect() as conn:
        conn.execute(text("SELECT 'automnal' AS a, 'xautomn' AS b")).all()
    assert seen == ["SELECT 'automnal' AS a, 'xautomn' AS b"]


@pytest.mark.parametrize("bad", ["", "1abc", "a-b", "a;drop", "a b", "x" * 129])
def test_schema_names_must_be_plain_identifiers(bad: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BSC_SCHEMA", raising=False)
    if bad == "":
        assert configured_schema(bad) == "automn"  # empty means not set
        return
    with pytest.raises(ValueError, match="schema name"):
        configured_schema(bad)
