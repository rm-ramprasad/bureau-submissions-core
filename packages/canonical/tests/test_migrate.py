"""Migration runner and view files (canonical.migrate). The real databases are tested in test_db_views.py (CI job)."""

import pytest
from sqlalchemy import text

from canonical import build_views
from canonical.db import make_engine
from canonical.migrate import (
    AZURE_MIGRATIONS,
    apply_migrations,
    apply_views,
    pending_migrations,
    split_batches,
    split_statements,
    view_files,
)

MIGRATIONS = [
    "001_baseline",
    "002_three_outputs",
    "003_filtered_unique_keys",
    "004_ref_code_labels",
    "005_new_input_workbooks",
    "006_generate_once",
    "007_reconciliation",
]


def test_split_statements_ignores_comments_and_blank_tail() -> None:
    sql = "-- header; comment\nCREATE TABLE a (x INT);\n\n-- next\nCREATE TABLE b (\n  y INT\n);\n"
    assert split_statements(sql) == ["CREATE TABLE a (x INT)", "CREATE TABLE b (\n  y INT\n)"]
    assert split_statements("SELECT 1") == ["SELECT 1"]


def test_split_batches_on_go_lines_only() -> None:
    sql = "-- only a comment\nGO\nCREATE VIEW a AS SELECT 'GO' AS x;\ngo\n  GO  \nCREATE VIEW b AS SELECT 1;\n"
    assert split_batches(sql) == ["CREATE VIEW a AS SELECT 'GO' AS x;", "CREATE VIEW b AS SELECT 1;"]


def test_runner_applies_each_file_once_in_the_schema(tmp_path) -> None:
    (tmp_path / "002_a.sql").write_text("CREATE TABLE automn.a (x INT);")
    (tmp_path / "003_b.sql").write_text("CREATE TABLE automn.b (y INT);\nINSERT INTO automn.b VALUES (1);")
    engine = make_engine(f"sqlite:///{tmp_path / 'm.db'}")
    assert apply_migrations(engine, tmp_path) == ["002_a", "003_b"]
    assert apply_migrations(engine, tmp_path) == []
    (tmp_path / "004_c.sql").write_text("CREATE TABLE automn.c (z INT);")
    assert apply_migrations(engine, tmp_path) == ["004_c"]
    with engine.connect() as conn:
        assert conn.execute(text("SELECT COUNT(*) FROM automn.b")).scalar() == 1
        assert conn.execute(text("SELECT COUNT(*) FROM automn.schema_migration")).scalar() == 3


def test_a_failing_file_leaves_nothing_behind(tmp_path) -> None:
    (tmp_path / "002_bad.sql").write_text("CREATE TABLE automn.a (x INT);\nNOT SQL;")
    engine = make_engine(f"sqlite:///{tmp_path / 'm.db'}")
    with pytest.raises(Exception, match="syntax error"):
        apply_migrations(engine, tmp_path)
    assert [p.stem for p in pending_migrations(engine, tmp_path)] == ["002_bad"]


def test_after_the_baseline_only_later_migrations_are_pending(tmp_path) -> None:
    assert sorted(p.stem for p in AZURE_MIGRATIONS.glob("*.sql")) == MIGRATIONS
    engine = make_engine(f"sqlite:///{tmp_path / 'm.db'}")
    with engine.begin() as conn:  # as on Azure: the administrator ran the baseline
        conn.execute(
            text(
                "CREATE TABLE automn.schema_migration (version VARCHAR(100) PRIMARY KEY, applied_at DATETIME NOT NULL)"
            )
        )
        conn.execute(text("INSERT INTO automn.schema_migration VALUES ('001_baseline', CURRENT_TIMESTAMP)"))
    assert [p.stem for p in pending_migrations(engine)] == MIGRATIONS[1:]


@pytest.mark.parametrize("dialect", ["azure", "postgres", "mysql"])
def test_view_files_run_functions_first(dialect: str) -> None:
    names = [p.name for p in view_files(dialect)]
    assert names == ["00_functions.sql", "01_reference.sql", "commercial.sql", "consumer.sql", "labels.sql"]


def test_unknown_dialects_are_refused(tmp_path) -> None:
    with pytest.raises(ValueError, match="unknown dialect"):
        view_files("oracle")
    with pytest.raises(ValueError, match="no source views for sqlite"):
        apply_views(make_engine(f"sqlite:///{tmp_path / 'm.db'}"))


# ------------------------------------------------------------------ generated Postgres / MySQL views


def test_generated_views_are_up_to_date() -> None:
    stale = [p for p, content in build_views.build().items() if not p.is_file() or p.read_text("utf-8") != content]
    assert not stale, "run  uv run python -m canonical.build_views  and commit: " + ", ".join(map(str, stale))


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT RIGHT('0' + CAST(x AS VARCHAR(2)), 2)",
        "SELECT TRY_CAST(x AS DECIMAL(20, 2))",
        "SELECT automn.fn_label_value('CONSUMER', 'gender', x)",
        "CREATE OR ALTER FUNCTION automn.f() RETURNS INT AS BEGIN RETURN 1; END",
        "SELECT * FROM a OUTER APPLY (VALUES (a.x)) AS d(x)",
    ],
)
def test_forbidden_forms_are_refused(sql: str) -> None:
    assert build_views.check_source("x.sql", sql)


def test_comments_and_numbers_are_not_refused() -> None:
    assert not build_views.check_source("x.sql", "-- never '...' + ..., never TRY_CAST\nSELECT a.source_row + 100000")


def test_build_views_check_passes_on_the_committed_files(monkeypatch, capsys) -> None:
    monkeypatch.setattr("sys.argv", ["build_views", "--check"])
    assert build_views.main() == 0
    assert "0 stale" in capsys.readouterr().out


def test_build_views_check_names_a_stale_file(monkeypatch, capsys) -> None:
    real = build_views.build
    monkeypatch.setattr(build_views, "build", lambda: {p: c + "-- changed\n" for p, c in real().items()})
    monkeypatch.setattr("sys.argv", ["build_views", "--check"])
    assert build_views.main() == 1
    assert "STALE postgres/views/consumer.sql" in capsys.readouterr().out


def test_cross_apply_becomes_cross_join_lateral() -> None:
    sql = "CREATE VIEW automn.v AS SELECT d.x FROM automn.a AS a CROSS APPLY (VALUES (a.x), (a.y)) AS d(x)"
    for dialect in ("postgres", "mysql"):
        out = build_views.translate("x.sql", sql, dialect)
        assert "CROSS JOIN LATERAL" in out and "INNER JOIN" not in out


def test_a_failed_file_is_named_and_a_hand_run_one_can_be_marked(tmp_path) -> None:
    from canonical.migrate import MigrationFailed, mark_applied

    (tmp_path / "002_a.sql").write_text("CREATE TABLE automn.a (x INT);")
    (tmp_path / "003_b.sql").write_text("CREATE TABLE automn.a (x INT);")  # as if run by hand already
    (tmp_path / "004_c.sql").write_text("CREATE TABLE automn.c (z INT);")
    engine = make_engine(f"sqlite:///{tmp_path / 'm.db'}")
    with pytest.raises(MigrationFailed) as failed:
        apply_migrations(engine, tmp_path)
    assert failed.value.version == "003_b" and failed.value.applied == ["002_a"]
    assert mark_applied(engine, "003_b", tmp_path) is True
    assert mark_applied(engine, "003_b", tmp_path) is False  # already recorded
    with pytest.raises(ValueError, match="No migration"):
        mark_applied(engine, "999_x", tmp_path)
    assert apply_migrations(engine, tmp_path) == ["004_c"]
