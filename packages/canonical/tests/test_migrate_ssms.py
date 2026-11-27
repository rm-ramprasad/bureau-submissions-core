"""The release's SSMS copies of the migrations (canonical.migrate.ssms_script): run once, all or nothing, recorded."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from canonical.migrate import AZURE_MIGRATIONS, BASELINE, main_ssms, ssms_script, write_ssms_scripts


def test_a_migration_is_wrapped_around_the_unchanged_file() -> None:
    path = AZURE_MIGRATIONS / "023_position_terms.sql"
    out = ssms_script(path)
    assert path.read_text("utf-8").rstrip() in out  # the frozen file, byte for byte
    lines = [ln for ln in out.splitlines() if not ln.startswith("--")]
    assert lines[:2] == [
        "SET XACT_ABORT ON;",
        "IF EXISTS (SELECT 1 FROM automn.schema_migration WHERE version = N'023_position_terms')",
    ]
    assert "BEGIN TRANSACTION;" in lines
    assert out.index("BEGIN TRANSACTION;") < out.index("ALTER TABLE") < out.index("INSERT INTO automn.schema_migration")
    assert out.rstrip().endswith("PRINT N'023_position_terms applied and recorded';")
    assert out.count("COMMIT;") == 1


def test_the_baseline_is_shipped_as_it_is() -> None:
    path = AZURE_MIGRATIONS / f"{BASELINE}.sql"
    assert ssms_script(path) == path.read_text("utf-8")  # it records itself and has its own transaction


def test_no_migration_after_the_baseline_has_its_own_transaction() -> None:
    """The wrapper adds BEGIN/COMMIT: a file with its own would commit half-way (the runner rule, kept for SSMS)."""
    for path in sorted(AZURE_MIGRATIONS.glob("*.sql")):
        if path.stem == BASELINE:
            continue
        code = [ln.strip().upper() for ln in path.read_text("utf-8").splitlines() if not ln.strip().startswith("--")]
        assert not any(re.match(r"(BEGIN\s+TRAN|COMMIT|ROLLBACK|GO)\b", ln) for ln in code), path.name


def test_every_migration_is_written(tmp_path: Path) -> None:
    written = write_ssms_scripts(tmp_path / "m")
    assert [p.name for p in written] == sorted(p.name for p in AZURE_MIGRATIONS.glob("*.sql"))
    assert all(p.read_text("utf-8") == ssms_script(AZURE_MIGRATIONS / p.name) for p in written)


def test_command_line(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main_ssms(["ssms", str(tmp_path)]) == 0
    assert "SSMS migration scripts written" in capsys.readouterr().out
    assert main_ssms(["x"]) == 2


def test_a_strange_file_name_is_refused(tmp_path: Path) -> None:
    bad = tmp_path / "025_x'; DROP TABLE y.sql"
    bad.write_text("SELECT 1;", encoding="utf-8")
    with pytest.raises(ValueError, match="NNN_lowercase_words"):
        ssms_script(bad)
