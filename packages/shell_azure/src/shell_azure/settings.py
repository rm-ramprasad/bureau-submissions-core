"""Runtime settings on Azure, read from environment variables only (no secrets in code). The names are the reference
app's, so the Function App's existing app settings keep working unchanged (SUBMISSIONS_VALIDATION_ENGINE is no longer
read: the core is the only engine).

On Azure every service is reached with the Function App's user-assigned managed identity (AZURE_CLIENT_ID); there
are no keys or connection strings. Locally, `az login` supplies the identity, SUBMISSIONS_SQL_ENV_FILE points at the
SQL login file (.env.sql, never committed), and SUBMISSIONS_STORAGE_CONNECTION_STRING can point at Azurite.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from sqlalchemy import URL

if TYPE_CHECKING:
    from app.api.auth import Groups, OidcConfig


def _flag(value: str | None, default: bool) -> bool:
    return default if value is None else value.strip().lower() not in ("0", "false", "no", "")


@dataclass(frozen=True)
class Settings:
    database_url: str | URL  # app tables in schema automn
    source_database_url: str | URL  # database holding the source views (defaults to database_url)
    output_dir: Path  # local file store root (storage = "local")
    tenant: str | None = None  # the one NBFC this deployment serves
    log_level: str = "INFO"
    storage: str = "local"  # "blob" on Azure, "local" for development
    storage_account_url: str | None = None  # https://<account>.blob.core.windows.net
    queue_account_url: str | None = None  # https://<account>.queue.core.windows.net
    storage_connection_string: str | None = None  # Azurite only; replaces the account URLs and the identity
    files_container: str = "files"
    files_prefix: str = "bureau-submissions"
    exports_container: str = "exports"
    dispatch: str = "none"  # "queue" on Azure, "none" locally (the polling worker)
    validate_queue: str = "validate-jobs"
    generate_queue: str = "generate-jobs"
    auth: str = "entra"  # "entra", "oidc" (any other OpenID Connect provider) or "none" (development)
    entra_tenant_id: str | None = None
    entra_client_id: str | None = None
    entra_user_group_id: str | None = None
    entra_admin_group_id: str | None = None
    app_user_fallback: bool = True
    oidc_issuer: str | None = None
    oidc_jwks_uri: str | None = None
    oidc_audience: tuple[str, ...] = ()
    oidc_groups_claim: str = "groups"
    oidc_user_group: str | None = None
    oidc_admin_group: str | None = None
    require_inputs: bool = True
    frontend_dir: Path | None = None  # local development: serve the static frontend
    retention_days: int = 90

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> Settings:
        e = dict(os.environ if env is None else env)
        get = e.get
        url: str | URL | None = get("SUBMISSIONS_DATABASE_URL")
        if not url and get("SUBMISSIONS_SQL_ENV_FILE"):  # local development: the SQL login file
            from shell_azure.sql import url_from_env_file

            url = url_from_env_file(Path(e["SUBMISSIONS_SQL_ENV_FILE"]))  # a URL object: its password is masked
        if not url:
            raise RuntimeError(
                "SUBMISSIONS_DATABASE_URL is not set (Azure: mssql+pyodbc://@<server>.database.windows.net:1433/"
                "<database>?driver=ODBC+Driver+18+for+SQL+Server&Encrypt=yes, managed identity), nor "
                "SUBMISSIONS_SQL_ENV_FILE (local: the .env.sql login file)"
            )
        tenant = get("SUBMISSIONS_TENANT")
        if not tenant:
            raise RuntimeError("SUBMISSIONS_TENANT is not set: the NBFC this deployment serves, e.g. rarfincare")
        frontend = get("SUBMISSIONS_FRONTEND_DIR")
        settings = cls(
            database_url=url,
            source_database_url=get("SUBMISSIONS_SOURCE_DATABASE_URL") or url,
            output_dir=Path(get("SUBMISSIONS_OUTPUT_DIR", "output")),
            tenant=tenant,
            log_level=get("SUBMISSIONS_LOG_LEVEL", "INFO"),
            storage=get("SUBMISSIONS_STORAGE", "local").lower(),
            storage_account_url=get("SUBMISSIONS_STORAGE_ACCOUNT_URL") or None,
            queue_account_url=get("SUBMISSIONS_QUEUE_ACCOUNT_URL") or None,
            storage_connection_string=get("SUBMISSIONS_STORAGE_CONNECTION_STRING") or None,
            files_container=get("SUBMISSIONS_FILES_CONTAINER", "files"),
            files_prefix=get("SUBMISSIONS_FILES_PREFIX", "bureau-submissions"),
            exports_container=get("SUBMISSIONS_EXPORTS_CONTAINER", "exports"),
            dispatch=get("SUBMISSIONS_DISPATCH", "none").lower(),
            validate_queue=get("SUBMISSIONS_VALIDATE_QUEUE", "validate-jobs"),
            generate_queue=get("SUBMISSIONS_GENERATE_QUEUE", "generate-jobs"),
            auth=get("SUBMISSIONS_AUTH", "entra").lower(),
            entra_tenant_id=get("SUBMISSIONS_ENTRA_TENANT_ID") or None,
            entra_client_id=get("SUBMISSIONS_ENTRA_CLIENT_ID") or None,
            entra_user_group_id=(get("SUBMISSIONS_ENTRA_USER_GROUP_ID") or "").lower() or None,
            entra_admin_group_id=(get("SUBMISSIONS_ENTRA_ADMIN_GROUP_ID") or "").lower() or None,
            app_user_fallback=_flag(get("SUBMISSIONS_APP_USER_FALLBACK"), True),
            oidc_issuer=get("SUBMISSIONS_OIDC_ISSUER") or None,
            oidc_jwks_uri=get("SUBMISSIONS_OIDC_JWKS_URI") or None,
            oidc_audience=tuple(a.strip() for a in (get("SUBMISSIONS_OIDC_AUDIENCE") or "").split(",") if a.strip()),
            oidc_groups_claim=get("SUBMISSIONS_OIDC_GROUPS_CLAIM") or "groups",
            oidc_user_group=(get("SUBMISSIONS_OIDC_USER_GROUP") or "").lower() or None,
            oidc_admin_group=(get("SUBMISSIONS_OIDC_ADMIN_GROUP") or "").lower() or None,
            require_inputs=_flag(get("SUBMISSIONS_REQUIRE_INPUTS"), True),
            frontend_dir=Path(frontend) if frontend else None,
            retention_days=int(get("SUBMISSIONS_RETENTION_DAYS", "90")),
        )
        settings.check()
        return settings

    def sign_in(self) -> tuple[OidcConfig | None, Groups]:
        """The token check and access groups for SUBMISSIONS_AUTH (None: no sign-in, or Entra not configured)."""
        from app.api.auth import Groups, OidcConfig

        if self.auth == "oidc" and self.oidc_issuer and self.oidc_jwks_uri:
            config = OidcConfig(
                issuers=(self.oidc_issuer,),
                jwks_uri=self.oidc_jwks_uri,
                audiences=self.oidc_audience,
                groups_claim=self.oidc_groups_claim,
            )
            return config, Groups(self.oidc_user_group, self.oidc_admin_group, self.app_user_fallback)
        groups = Groups(self.entra_user_group_id, self.entra_admin_group_id, self.app_user_fallback)
        if self.auth == "entra" and self.entra_tenant_id and self.entra_client_id:
            return OidcConfig.entra(self.entra_tenant_id, self.entra_client_id), groups
        return None, groups

    def check(self) -> None:
        for name, value, allowed in (
            ("SUBMISSIONS_STORAGE", self.storage, ("blob", "local")),
            ("SUBMISSIONS_DISPATCH", self.dispatch, ("queue", "none")),
            ("SUBMISSIONS_AUTH", self.auth, ("entra", "oidc", "none")),
        ):
            if value not in allowed:
                raise RuntimeError(f"{name} must be one of {', '.join(allowed)}, not {value!r}")
        if self.auth == "oidc" and not (self.oidc_issuer and self.oidc_jwks_uri and self.oidc_audience):
            raise RuntimeError(
                "SUBMISSIONS_AUTH=oidc needs SUBMISSIONS_OIDC_ISSUER, SUBMISSIONS_OIDC_JWKS_URI and SUBMISSIONS_OIDC_AUDIENCE"
            )
        if self.retention_days < 1:
            raise RuntimeError("SUBMISSIONS_RETENTION_DAYS must be at least 1")
