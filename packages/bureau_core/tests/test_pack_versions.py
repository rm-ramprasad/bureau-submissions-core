"""Every change to a type's rule and format files is a new pack version in its CHANGES.md (owner, 2026-11-27)."""

from __future__ import annotations

from pathlib import Path

import pytest

from bureau_core.types import (
    CHANGES,
    PACK_FILES,
    TypeDefinitionError,
    descriptors,
    pack_checksum,
    pack_history,
    pack_version,
)

LIVE = {name: folder for name, (d, folder) in descriptors().items() if not d.planned}


@pytest.mark.parametrize("name", sorted(LIVE))
def test_the_newest_entry_matches_the_files(name: str) -> None:
    folder = LIVE[name]
    newest, actual = pack_history(folder)[0], pack_checksum(folder)
    assert newest.checksum == actual, (
        f"{name}: the rule or format files changed but {folder.name}/{CHANGES} has no new version. Add on top:\n"
        f"## <next version> (<today>) — checksum {actual}\n\n<what changed and why>"
    )


@pytest.mark.parametrize("name", sorted(LIVE))
def test_versions_only_go_up_and_checksums_are_not_reused(name: str) -> None:
    history = pack_history(LIVE[name])
    keys = [tuple(int(p) for p in e.version.split(".")) for e in history]
    assert keys == sorted(keys, reverse=True) and len(set(keys)) == len(keys), f"{name}: versions must go up"
    assert [e.date for e in history] == sorted((e.date for e in history), reverse=True)
    assert len({e.checksum for e in history}) == len(history), f"{name}: a checksum appears twice"


def test_pack_version_is_the_newest_entry() -> None:
    assert pack_version("CONSUMER") == pack_history(LIVE["CONSUMER"])[0]
    assert pack_version("CONSUMER").version == "2026.1"


def _folder(tmp_path: Path, changes: str | None) -> Path:
    (tmp_path / "rules.yaml").write_text("rules: []\n", encoding="utf-8")
    if changes is not None:
        (tmp_path / CHANGES).write_text(changes, encoding="utf-8")
    return tmp_path


def test_any_pack_file_change_changes_the_checksum(tmp_path: Path) -> None:
    folder = _folder(tmp_path, None)
    before = pack_checksum(folder)
    (folder / "rules.yaml").write_text("rules: [x]\n", encoding="utf-8")
    assert pack_checksum(folder) != before
    (folder / "type.yaml").write_text("label: not part of the pack\n", encoding="utf-8")
    after = pack_checksum(folder)
    assert "type.yaml" not in PACK_FILES and pack_checksum(folder) == after


def test_line_endings_do_not_change_the_checksum(tmp_path: Path) -> None:
    folder = _folder(tmp_path, None)
    unix = pack_checksum(folder)
    (folder / "rules.yaml").write_bytes(b"rules: []\r\n")
    assert pack_checksum(folder) == unix  # a Windows checkout gives the same version


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        (None, "missing"),
        ("# x\n\nno entries\n", "no version entry"),
        ("## 2026.1 checksum abc\n", "heading not as"),
    ],
)
def test_a_bad_ledger_is_refused(tmp_path: Path, changes: str | None, message: str) -> None:
    with pytest.raises(TypeDefinitionError, match=message):
        pack_history(_folder(tmp_path, changes))


def test_runs_never_fail_over_the_ledger(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Outside CI (e.g. a client's own type folder) a missing or stale ledger gives `unversioned` + the real checksum."""
    import bureau_core.types as types

    folder = _folder(tmp_path, None)
    monkeypatch.setattr(types, "descriptors", lambda: {"SAMPLE": (None, folder)})
    types.pack_version.cache_clear()
    try:
        assert types.pack_version("SAMPLE") == types.PackVersion(types.UNVERSIONED, "", pack_checksum(folder))
        (folder / CHANGES).write_text("## 2026.1 (2026-11-27) — checksum 000000000000\n", encoding="utf-8")
        types.pack_version.cache_clear()
        assert types.pack_version("SAMPLE").version == types.UNVERSIONED  # stale entry
        (folder / CHANGES).write_text(f"## 2026.1 (2026-11-27) — checksum {pack_checksum(folder)}\n", encoding="utf-8")
        types.pack_version.cache_clear()
        assert types.pack_version("SAMPLE").version == "2026.1"
    finally:
        types.pack_version.cache_clear()
