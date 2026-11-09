"""L2b account position contract (the real views run in test_db_views.py, CI job "Database")."""

import re
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import text

from canonical.db import make_engine
from canonical.migrate import AZURE_MIGRATIONS
from canonical.position import POSITION_COLUMNS, UPLOAD_COLUMNS, check_view, read_positions, stg_lms_position

SOURCE = (AZURE_MIGRATIONS / "views" / "position.sql").read_text(encoding="utf-8")


def _view_columns(name: str) -> list[str]:
    body = SOURCE.split(f"CREATE OR ALTER VIEW automn.{name} AS", 1)[1].split("\nFROM", 1)[0]
    return re.findall(r"\bAS (\w+),?\s*(?:--.*)?$", body, re.M)


@pytest.mark.parametrize("view", ["vw_pos_lms_consumer", "vw_pos_lms_commercial", "vw_pos_upload"])
def test_each_part_returns_the_contract_columns_in_order(view: str) -> None:
    assert _view_columns(view) == list(POSITION_COLUMNS)


def test_upload_table_has_every_figure_of_the_contract() -> None:
    figures = set(POSITION_COLUMNS) - {"account_number", "sanction_number", "as_of_date", "source", "source_load_id"}
    assert set(UPLOAD_COLUMNS) == figures | {"account_number", "sanction_number", "position_date"}
    assert [c.name for c in stg_lms_position.columns][:2] == ["load_id", "source_row"]
    migration = (AZURE_MIGRATIONS / "012_lms_position_upload.sql").read_text()
    assert all(f"\t{c} NVARCHAR(100) NULL," in migration for c in UPLOAD_COLUMNS)


@pytest.fixture
def eng(tmp_path):
    engine = make_engine(f"sqlite:///{tmp_path / 'p.db'}")

    def row(account: str, day: str, balance: str) -> str:
        values = {c: "NULL" for c in POSITION_COLUMNS}
        values |= {
            "account_number": f"'{account}'",
            "sanction_number": "''",
            "as_of_date": f"'{day}'",
            "current_balance": balance,
            "source": "'VIEW'",
        }
        return "SELECT " + ", ".join(f"{v} AS {c}" for c, v in values.items())

    with engine.begin() as conn:
        conn.execute(
            text(
                "CREATE VIEW automn.vw_pos_account_position AS "
                + " UNION ALL ".join(
                    [row("A2", "2026-10-31", "20.5"), row("A1", "2026-10-31", "10"), row("A1", "2026-09-30", "9")]
                )
            )
        )
        conn.execute(text("CREATE VIEW automn.vw_pos_bad AS SELECT 1 AS account_number, 2 AS extra"))
        conn.execute(
            text(
                "CREATE VIEW automn.vw_pos_reordered AS SELECT "
                + ", ".join(reversed([f"NULL AS {c}" for c in POSITION_COLUMNS]))
            )
        )
    return engine


def test_read_positions_for_one_date(eng) -> None:
    rows = read_positions(eng, date(2026, 10, 31))
    assert [(r["account_number"], Decimal(str(r["current_balance"]))) for r in rows] == [
        ("A1", Decimal(10)),
        ("A2", Decimal("20.5")),
    ]
    assert list(rows[0]) == list(POSITION_COLUMNS)
    assert [r["account_number"] for r in read_positions(eng, date(2026, 10, 31), ["A2", "ZZ"])] == ["A2"]
    assert read_positions(eng, date(2026, 10, 31), []) == []


def test_check_view(eng) -> None:
    assert check_view(eng) == []
    problems = check_view(eng, "vw_pos_bad")
    assert "vw_pos_bad: column extra is not in the contract" in problems
    assert "vw_pos_bad: missing column as_of_date" in problems
    assert check_view(eng, "vw_pos_reordered") == ["vw_pos_reordered: columns are in another order than the contract"]
