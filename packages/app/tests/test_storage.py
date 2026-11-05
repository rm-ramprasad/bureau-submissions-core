"""The file-store port's local implementation (the Blob store is tested in shell_azure)."""

import pytest

from app.ports.files import LocalFileStore, ReadableStores, StoredFileExists, StoredFileNotFound

BLOB = "https://acct.blob.core.windows.net/files/x.txt"


@pytest.mark.parametrize("bad", ["", "../x.txt", "t/../../x", "t//x", "./x"])
def test_keys_cannot_escape(tmp_path, bad):
    with pytest.raises(ValueError):
        LocalFileStore(tmp_path).put(bad, b"x", "text/plain")


def test_local_store(tmp_path):
    local = LocalFileStore(tmp_path)
    uri = local.put("t/b/f.txt", b"abc", "text/plain")
    assert b"".join(local.read_chunks(uri)) == b"abc" and local.owns(uri)
    with pytest.raises(StoredFileExists):
        local.put("t/b/f.txt", b"again", "text/plain")
    assert not local.owns(str(tmp_path.parent / "secret.txt"))
    assert not local.owns(BLOB)
    assert local.download_url(uri, "f.txt", "text/plain") is None  # streamed by the app
    assert "local folder" in local.describe()
    local.delete(uri)
    local.delete(uri)  # already gone: no error
    with pytest.raises(StoredFileNotFound):
        list(local.read_chunks(uri))
    with pytest.raises(StoredFileNotFound):
        local.download_url(uri, "f.txt", "text/plain")


def test_readable_stores_write_to_the_first_and_read_from_all(tmp_path):
    new, old = LocalFileStore(tmp_path / "new"), LocalFileStore(tmp_path / "old")
    stores = ReadableStores(new, old)
    legacy = old.put("a.txt", b"old", "text/plain")
    fresh = stores.put("b.txt", b"new", "text/plain")
    assert new.owns(fresh) and stores.owns(legacy)
    assert b"".join(stores.read_chunks(legacy)) == b"old"
    assert stores.download_url(legacy, "a.txt", "text/plain") is None
    assert " or " in stores.describe()
    stores.delete(legacy)
    assert not (tmp_path / "old" / "a.txt").exists()
    stores.delete(BLOB)  # not ours: ignored
    assert not stores.owns(BLOB)
    with pytest.raises(StoredFileNotFound):
        list(stores.read_chunks(BLOB))
    with pytest.raises(StoredFileNotFound):
        stores.download_url(BLOB, "x", "text/plain")
