"""The release's package-contents check (deploy/check_package.py) on hand-made zips."""

from __future__ import annotations

import importlib.util
import zipfile
from pathlib import Path

import pytest

_spec = importlib.util.spec_from_file_location("check_package", Path(__file__).parent.parent / "check_package.py")
assert _spec and _spec.loader
cp = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cp)

S = cp.SITE
GOOD = [
    "function_app.py",
    "host.json",
    S + "shell_azure/__init__.py",
    S + "shell_azure/VERSION",
    S + "app/__init__.py",
    S + "bureau_core/__init__.py",
    S + "canonical/__init__.py",
    S + "canonical/migrations/azure/views/consumer.sql",
    S + "clientconfig/__init__.py",
    S + "sources/__init__.py",
    S + "flask/__init__.py",
]


def _dist(tmp_path: Path, function: list[str], migrations: list[str] | None = None) -> Path:
    for name, entries in (("function.zip", function), ("migrations.zip", migrations or ["001_baseline.sql"])):
        with zipfile.ZipFile(tmp_path / name, "w") as z:
            for e in entries:
                z.writestr(e, "")
    return tmp_path


def test_the_repository_packages_are_found() -> None:
    assert {"shell_azure", "app", "bureau_core", "canonical", "clientconfig", "sources"} <= cp.repo_package_names()


def test_a_good_azure_package_passes(tmp_path: Path) -> None:
    assert cp.check("azure", _dist(tmp_path, GOOD)) == []


@pytest.mark.parametrize(
    ("extra", "message"),
    [
        (S + "shell_aws/__init__.py", "another cloud's or a dropped library: shell_aws"),
        (S + "boto3/__init__.py", "another cloud's or a dropped library: boto3"),
        (S + "botocore/__init__.py", "another cloud's or a dropped library: botocore"),
        (S + "sqlglot/__init__.py", "another cloud's or a dropped library: sqlglot"),
        ("template.yaml", "AWS template: template.yaml"),
        (S + "app/.env.sql", "secret: "),
        ("local.settings.json", "secret: "),
        (S + "canonical/migrations/postgres/views/consumer.sql", "dropped database dialect"),
    ],
)
def test_foreign_parts_fail(tmp_path: Path, extra: str, message: str) -> None:
    problems = cp.check("azure", _dist(tmp_path, [*GOOD, extra]))
    assert any(message in p for p in problems), problems


def test_an_unlisted_repository_package_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cp, "repo_package_names", lambda: {"app", "shell_azure", "lms_mapping"})
    problems = cp.check("azure", _dist(tmp_path, [*GOOD, S + "lms_mapping/__init__.py"]))
    assert problems == ["function.zip: repository package the azure shell does not depend on: lms_mapping"]


def test_missing_parts_fail(tmp_path: Path) -> None:
    problems = cp.check("azure", _dist(tmp_path, [n for n in GOOD if "shell_azure" not in n and n != "host.json"]))
    assert "function.zip: missing shell_azure" in problems
    assert "function.zip: missing host.json" in problems


def test_migrations_hold_only_top_level_sql(tmp_path: Path) -> None:
    problems = cp.check("azure", _dist(tmp_path, GOOD, ["002_x.sql", "views/consumer.sql", "notes.md"]))
    assert problems == [
        "migrations.zip: not a top-level .sql file: views/consumer.sql",
        "migrations.zip: not a top-level .sql file: notes.md",
    ]


def test_unknown_shell_and_missing_zips(tmp_path: Path) -> None:
    assert cp.check("gcp", tmp_path) == ["unknown shell 'gcp': one of azure"]
    assert cp.check("azure", tmp_path) == ["function.zip not built", "migrations.zip not built"]


def test_main_exit_codes(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert cp.main(["check_package.py", "azure", str(_dist(tmp_path, GOOD))]) == 0
    assert "azure package: OK" in capsys.readouterr().out
    assert cp.main(["check_package.py", "azure", str(_dist(tmp_path, [*GOOD, S + "boto3/x.py"]))]) == 1
    assert cp.main(["check_package.py"]) == 2
