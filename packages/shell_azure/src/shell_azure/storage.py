"""Azure Blob Storage for generated files and CSV exports (the app's FileStore and Exports ports).

A stored file is identified by its blob URL, saved on submission_request:
  https://<account>.blob.core.windows.net/<container>/<prefix>/<tenant>/<batch>/<file>
Files are written once and never overwritten. The container stays private: downloads go through short-lived
user-delegation SAS links (signed with the managed identity, no account key) handed out and audited by the API.
"""

from __future__ import annotations

import contextlib
import secrets
from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import unquote

from app.ports.files import FileStore, LocalFileStore, ReadableStores, StoredFileExists, StoredFileNotFound, _safe_key

DOWNLOAD_URL_SECONDS = 60

# ---------------------------------------------------------------- Azure Blob Storage
# signer(container, blob_name, file_name, content_type, seconds) -> URL with a read-only SAS
Signer = Callable[[str, str, str, str, int], str]


class SasSigner:
    """Read-only blob SAS links. With a token credential (Azure): user-delegation SAS, the delegation key cached and
    renewed before it expires (needs Storage Blob Delegator). With a connection string (Azurite): account-key SAS."""

    KEY_LIFETIME = timedelta(hours=6)

    def __init__(self, service: Any, clock: Callable[[], datetime] = lambda: datetime.now(UTC)) -> None:
        self.service, self.clock = service, clock
        self._key: Any = None
        self._key_expiry: datetime = datetime.min.replace(tzinfo=UTC)

    def _delegation_key(self) -> Any:
        now = self.clock()
        if self._key is None or self._key_expiry - now < timedelta(minutes=10):
            self._key_expiry = now + self.KEY_LIFETIME
            self._key = self.service.get_user_delegation_key(now - timedelta(minutes=5), self._key_expiry)
        return self._key

    def __call__(self, container: str, blob_name: str, file_name: str, content_type: str, seconds: int) -> str:
        from azure.storage.blob import BlobSasPermissions, generate_blob_sas

        now = self.clock()
        account_key = getattr(self.service.credential, "account_key", None)
        signing = {"account_key": account_key} if account_key else {"user_delegation_key": self._delegation_key()}
        sas = generate_blob_sas(
            self.service.account_name,
            container,
            blob_name,
            permission=BlobSasPermissions(read=True),
            start=now - timedelta(minutes=1),
            expiry=now + timedelta(seconds=seconds),
            content_type=content_type,
            content_disposition=f'attachment; filename="{file_name}"',
            **signing,
        )
        return f"{self.service.get_blob_client(container, blob_name).url}?{sas}"


class BlobFileStore:
    """Private container; encryption at rest is Azure Storage's own (Microsoft-managed keys)."""

    def __init__(self, container: Any, prefix: str, signer: Signer) -> None:
        """container: azure.storage.blob.ContainerClient."""
        self.container, self.prefix, self.signer = container, prefix.strip("/"), signer
        self.base: str = str(container.url).rstrip("/") + "/"

    def _blob_name(self, relative_key: str) -> str:
        key = _safe_key(relative_key)
        return f"{self.prefix}/{key}" if self.prefix else key

    def _name_of(self, uri: str) -> str:
        return unquote(uri[len(self.base) :])

    def put(self, relative_key: str, data: bytes, content_type: str) -> str:
        from azure.core.exceptions import ResourceExistsError
        from azure.storage.blob import ContentSettings

        blob = self.container.get_blob_client(self._blob_name(relative_key))
        try:  # overwrite=False: conditional create, If-None-Match: *
            blob.upload_blob(data, overwrite=False, content_settings=ContentSettings(content_type=content_type))
        except ResourceExistsError as exc:
            raise StoredFileExists(blob.url) from exc
        return str(blob.url)

    def owns(self, uri: str) -> bool:
        if not uri.startswith(self.base):
            return False
        name = self._name_of(uri)
        return ".." not in name.split("/") and (not self.prefix or name.startswith(self.prefix + "/"))

    def describe(self) -> str:
        return self.base + (self.prefix + "/" if self.prefix else "")

    def read_chunks(self, uri: str) -> Iterator[bytes]:
        from azure.core.exceptions import ResourceNotFoundError

        if not self.owns(uri):
            raise StoredFileNotFound(uri)
        try:
            yield from self.container.get_blob_client(self._name_of(uri)).download_blob().chunks()
        except ResourceNotFoundError as exc:
            raise StoredFileNotFound(uri) from exc

    def delete(self, uri: str) -> None:
        from azure.core.exceptions import ResourceNotFoundError

        if self.owns(uri):
            with contextlib.suppress(ResourceNotFoundError):
                self.container.get_blob_client(self._name_of(uri)).delete_blob()

    def download_url(self, uri: str, file_name: str, content_type: str) -> str | None:
        """Read-only SAS valid for DOWNLOAD_URL_SECONDS: the browser downloads straight from the private container."""
        if not self.owns(uri):
            raise StoredFileNotFound(uri)
        name = self._name_of(uri)
        if not self.container.get_blob_client(name).exists():
            raise StoredFileNotFound(uri)
        return self.signer(self.container.container_name, name, file_name, content_type, DOWNLOAD_URL_SECONDS)


class BlobExports:
    """CSV exports: written under a random name to the exports container (a lifecycle rule deletes them after a day)
    and handed out as a short-lived link, which avoids the API's response size and time limits."""

    URL_SECONDS = 300

    def __init__(self, container: Any, signer: Signer) -> None:
        self.container, self.signer = container, signer

    def publish(self, file_name: str, data: bytes, content_type: str) -> str:
        from azure.storage.blob import ContentSettings

        name = f"{secrets.token_hex(16)}/{_safe_key(file_name)}"
        self.container.get_blob_client(name).upload_blob(
            data, content_settings=ContentSettings(content_type=content_type)
        )
        return self.signer(self.container.container_name, name, file_name, content_type, self.URL_SECONDS)


def blob_service(settings: Any) -> Any:
    from azure.storage.blob import BlobServiceClient

    from shell_azure.sql import credential

    if settings.storage_connection_string:
        return BlobServiceClient.from_connection_string(settings.storage_connection_string)
    if not settings.storage_account_url:
        raise RuntimeError("SUBMISSIONS_STORAGE_ACCOUNT_URL is required when SUBMISSIONS_STORAGE=blob")
    return BlobServiceClient(settings.storage_account_url, credential=credential())


def make_store(settings: Any) -> FileStore:
    """The store new files are written to, able to read the app's other own locations too."""
    if settings.storage == "local":
        return LocalFileStore(settings.output_dir)
    if settings.storage != "blob":
        raise RuntimeError(f"SUBMISSIONS_STORAGE must be 'blob' or 'local', not {settings.storage!r}")
    service = blob_service(settings)
    primary = BlobFileStore(
        service.get_container_client(settings.files_container), settings.files_prefix, SasSigner(service)
    )
    return ReadableStores(primary, LocalFileStore(settings.output_dir))


def make_exports(settings: Any) -> BlobExports | None:
    """None: exports are streamed by the API (local development and tests)."""
    if settings.storage != "blob":
        return None
    service = blob_service(settings)
    return BlobExports(service.get_container_client(settings.exports_container), SasSigner(service))
