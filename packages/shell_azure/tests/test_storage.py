"""Blob storage for files and exports (shell_azure.storage), with fake Azure clients."""

import re
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlsplit

import pytest
from azure.core.exceptions import ResourceExistsError, ResourceNotFoundError

from app.ports.files import LocalFileStore, StoredFileExists, StoredFileNotFound
from shell_azure.settings import Settings
from shell_azure.storage import BlobExports, BlobFileStore, SasSigner, make_exports, make_store

ACCOUNT = "https://stbureau.blob.core.windows.net"


class _Download:
    def __init__(self, data):
        self.data = data

    def chunks(self):
        yield from (self.data[i : i + 4] for i in range(0, len(self.data), 4))


class FakeBlob:
    def __init__(self, container, name):
        self.container, self.name = container, name
        self.url = f"{container.url}/{name}"

    def upload_blob(self, data, overwrite=True, content_settings=None):
        if not overwrite and self.name in self.container.blobs:
            raise ResourceExistsError("BlobAlreadyExists")
        self.container.blobs[self.name] = bytes(data)
        self.container.uploads.append(
            {"name": self.name, "overwrite": overwrite, "content_type": content_settings.content_type}
        )

    def download_blob(self):
        if self.name not in self.container.blobs:
            raise ResourceNotFoundError("BlobNotFound")
        return _Download(self.container.blobs[self.name])

    def delete_blob(self):
        if self.container.blobs.pop(self.name, None) is None:
            raise ResourceNotFoundError("BlobNotFound")

    def exists(self):
        return self.name in self.container.blobs


class FakeContainer:
    def __init__(self, name="files"):
        self.container_name, self.url = name, f"{ACCOUNT}/{name}"
        self.blobs: dict[str, bytes] = {}
        self.uploads: list[dict] = []

    def get_blob_client(self, name):
        return FakeBlob(self, name)


def fake_signer(container, blob_name, file_name, content_type, seconds):
    return f"{ACCOUNT}/{container}/{blob_name}?se={seconds}&rsct={content_type}&sig=x"


@pytest.fixture
def container():
    return FakeContainer()


@pytest.fixture
def store(container):
    return BlobFileStore(container, "bureau-submissions", fake_signer)


# ---------------------------------------------------------------- stores
def test_blob_put_read_delete(store, container):
    uri = store.put("nbfc_001/b1/CIB0000001-01102026-090000.txt", b"HD...TRLR", "text/plain")
    assert uri == f"{ACCOUNT}/files/bureau-submissions/nbfc_001/b1/CIB0000001-01102026-090000.txt"
    assert container.uploads[0] == {
        "name": "bureau-submissions/nbfc_001/b1/CIB0000001-01102026-090000.txt",
        "overwrite": False,
        "content_type": "text/plain",
    }
    assert b"".join(store.read_chunks(uri)) == b"HD...TRLR"
    store.delete(uri)
    store.delete(uri)  # already gone: no error
    with pytest.raises(StoredFileNotFound):
        list(store.read_chunks(uri))


def test_blob_never_overwrites(store):
    store.put("t/b/f.txt", b"one", "text/plain")
    with pytest.raises(StoredFileExists):
        store.put("t/b/f.txt", b"two", "text/plain")


def test_blob_owns_only_its_container_and_prefix(store):
    assert store.owns(f"{ACCOUNT}/files/bureau-submissions/t/b/f.txt")
    assert not store.owns(f"{ACCOUNT}/other/bureau-submissions/t/b/f.txt")
    assert not store.owns(f"{ACCOUNT}/files/elsewhere/f.txt")
    assert not store.owns(f"{ACCOUNT}/files/bureau-submissions/../elsewhere/f.txt")
    assert not store.owns("https://evil.blob.core.windows.net/files/bureau-submissions/t/b/f.txt")
    assert not store.owns("C:/output/t/b/f.txt")
    with pytest.raises(StoredFileNotFound):
        list(store.read_chunks(f"{ACCOUNT}/other/bureau-submissions/x"))


def test_blob_download_url_is_a_short_read_link(store):
    uri = store.put("t/b/f.txt", b"x", "text/plain")
    assert (
        store.download_url(uri, "f.txt", "text/plain")
        == f"{ACCOUNT}/files/bureau-submissions/t/b/f.txt?se=60&rsct=text/plain&sig=x"
    )
    with pytest.raises(StoredFileNotFound):
        store.download_url(f"{ACCOUNT}/files/bureau-submissions/t/b/missing.txt", "m.txt", "text/plain")


@pytest.mark.parametrize("bad", ["", "../x.txt", "t/../../x", "t//x", "./x"])
def test_keys_cannot_escape(store, tmp_path, bad):
    for s in (store, LocalFileStore(tmp_path)):
        with pytest.raises(ValueError):
            s.put(bad, b"x", "text/plain")


def test_local_store(tmp_path):
    local = LocalFileStore(tmp_path)
    uri = local.put("t/b/f.txt", b"abc", "text/plain")
    assert b"".join(local.read_chunks(uri)) == b"abc" and local.owns(uri)
    with pytest.raises(StoredFileExists):
        local.put("t/b/f.txt", b"again", "text/plain")
    assert not local.owns(str(tmp_path.parent / "secret.txt"))
    assert not local.owns(f"{ACCOUNT}/files/x.txt")


def test_make_store_and_exports(tmp_path):
    with pytest.raises(RuntimeError, match="SUBMISSIONS_STORAGE_ACCOUNT_URL"):
        make_store(Settings("sqlite://", "sqlite://", tmp_path, storage="blob"))
    with pytest.raises(RuntimeError, match="'blob' or 'local'"):
        make_store(Settings("sqlite://", "sqlite://", tmp_path, storage="ftp"))
    assert isinstance(make_store(Settings("sqlite://", "sqlite://", tmp_path)), LocalFileStore)
    assert make_exports(Settings("sqlite://", "sqlite://", tmp_path)) is None


def test_make_store_blob_reads_local_folder_too(tmp_path):
    azurite = (
        "DefaultEndpointsProtocol=http;AccountName=devstoreaccount1;AccountKey="
        "Eby8vdM02xNOcqFlqUwJPLlmEtlCDXJ1OUzFT50uSRZ6IFsuFq2UVErCz4I6tq/K1SZFPTOtr/KBHBeksoGMGw==;"
        "BlobEndpoint=http://127.0.0.1:10000/devstoreaccount1;"
    )
    store = make_store(Settings("sqlite://", "sqlite://", tmp_path, storage="blob", storage_connection_string=azurite))
    base = "http://127.0.0.1:10000/devstoreaccount1/files/bureau-submissions/"
    assert store.describe() == f"{base} or local folder {tmp_path.resolve()}"
    assert store.owns(str(tmp_path / "x.txt")) and store.owns(base + "a/b.txt")


# ---------------------------------------------------------------- SAS signing
class FakeService:
    account_name = "stbureau"

    def __init__(self, account_key=None):
        self.credential = type("C", (), {"account_key": account_key})() if account_key else object()
        self.key_requests = []

    def get_user_delegation_key(self, start, expiry):
        from azure.storage.blob import UserDelegationKey

        self.key_requests.append((start, expiry))
        key = UserDelegationKey()
        key.signed_oid, key.signed_tid = "oid", "tid"
        key.signed_start, key.signed_expiry = (
            start.strftime("%Y-%m-%dT%H:%M:%SZ"),
            expiry.strftime("%Y-%m-%dT%H:%M:%SZ"),
        )
        key.signed_service, key.signed_version, key.value = "b", "2025-01-05", "c2VjcmV0"
        return key

    def get_blob_client(self, container, name):
        return type("B", (), {"url": f"{ACCOUNT}/{container}/{name}"})()


def test_user_delegation_sas_is_read_only_short_and_reuses_the_key():
    now = [datetime(2026, 10, 28, 9, 0, tzinfo=UTC)]
    service = FakeService()
    signer = SasSigner(service, clock=lambda: now[0])
    url = signer("files", "p/t/b/f.txt", "f.txt", "text/plain", 60)
    q = parse_qs(urlsplit(url).query)
    assert url.startswith(f"{ACCOUNT}/files/p/t/b/f.txt?")
    assert q["sp"] == ["r"] and q["se"] == ["2026-10-28T09:01:00Z"] and q["skoid"] == ["oid"]
    assert q["rscd"] == ['attachment; filename="f.txt"'] and q["rsct"] == ["text/plain"]
    signer("files", "x", "x", "text/plain", 60)
    assert len(service.key_requests) == 1  # delegation key cached
    now[0] += timedelta(hours=6)
    signer("files", "x", "x", "text/plain", 60)
    assert len(service.key_requests) == 2  # renewed before it expires


def test_account_key_sas_for_azurite():
    service = FakeService(account_key="c2VjcmV0")
    q = parse_qs(urlsplit(SasSigner(service)("files", "x.txt", "x.txt", "text/csv", 300)).query)
    assert q["sp"] == ["r"] and "skoid" not in q and service.key_requests == []


def test_exports_are_written_under_a_random_name_and_linked(container):
    exports = BlobExports(container, fake_signer)
    url = exports.publish("findings-nbfc_001.csv", b"rule_id,outcome\n", "text/csv")
    (name,) = container.blobs
    assert re.fullmatch(r"[0-9a-f]{32}/findings-nbfc_001\.csv", name) and container.blobs[name].startswith(b"rule_id")
    assert url == f"{ACCOUNT}/files/{name}?se=300&rsct=text/csv&sig=x"


# ---------------------------------------------------------------- end to end on Blob Storage
