"""Who is calling the API.

Sign-in is generic OpenID Connect (step 3): the browser signs in at an OIDC provider (authorization code + PKCE) and
sends the token as `Authorization: Bearer <token>`. The app verifies it itself against an `OidcConfig`: signature
(the provider's published keys, JWKS), issuer, audience, expiry and any fixed claims. Microsoft Entra ID is one such
configuration (`OidcConfig.entra`: the tenant's keys and issuers, audience = the app registration as client id or
api://<client id>, `tid` = the tenant); any other provider (Cognito, Keycloak, Okta, ...) is `OidcConfig(...)` with
its issuer, JWKS address, audience and groups claim. On Azure, App Service authentication (Easy Auth) also rejects
calls without a valid token before the app runs; the endpoint is public, so the app's own check is what counts.

Who may use the app:
- With groups configured (`Groups`; the provider puts the caller's groups in the token, claim `groups` for Entra,
  e.g. `cognito:groups` for Cognito): members of either group get in, admin group members are administrators. IT
  manages access at the provider. Their app_user row is created at first sign-in (name, e-mail, last sign-in); an
  app_user disabled in the app stays locked out (emergency stop). While the app-user fallback is on (the
  switch-over), active app users outside the groups still get in, with their app_user admin flag.
- Without groups: the signed-in e-mail must belong to an active app_user (who may use the app, and who is an
  administrator).

auth = "none" (local development and tests): no sign-in; a fronting proxy may pass X-Forwarded-User.

Roles (`ROLES`) are defined for maker-checker (step 4 on): viewer, maker, approver, admin, auditor. Today every
signed-in user holds viewer, maker and approver, administrators also admin and auditor; only `admin` is enforced
(`admin_only`), so access is unchanged until roles get their own groups.

The shell picks one with `identity_provider(...)` and hands it to create_app: the port is any callable that returns
the caller's Identity for the current request (or aborts 401 / 403).
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from functools import cache, wraps
from typing import Any, NoReturn, cast

import jwt
from flask import abort, current_app, g, request

from app import audit
from app.users import UserRepository

LOGIN = "https://login.microsoftonline.com"

ROLES = ("viewer", "maker", "approver", "admin", "auditor")
USER_ROLES = frozenset({"viewer", "maker", "approver"})
ADMIN_ROLES = frozenset(ROLES)


@dataclass(frozen=True)
class Identity:
    username: str  # recorded on requests, exceptions and the audit trail
    display_name: str
    email: str | None
    is_admin: bool

    @property
    def roles(self) -> frozenset[str]:
        return ADMIN_ROLES if self.is_admin else USER_ROLES


@dataclass(frozen=True)
class OidcConfig:
    """How to verify a provider's tokens. `claims`: values the token must carry (e.g. Entra's tenant `tid`)."""

    issuers: tuple[str, ...]
    jwks_uri: str
    audiences: tuple[str, ...]
    groups_claim: str = "groups"
    claims: Mapping[str, str] = field(default_factory=dict)
    algorithms: tuple[str, ...] = ("RS256",)

    @classmethod
    def entra(cls, tenant_id: str, client_id: str) -> OidcConfig:
        """Microsoft Entra ID, one tenant: v2 (ID or access) and v1 access tokens for the app registration."""
        return cls(
            issuers=(f"{LOGIN}/{tenant_id}/v2.0", f"https://sts.windows.net/{tenant_id}/"),
            jwks_uri=f"{LOGIN}/{tenant_id}/discovery/v2.0/keys",
            audiences=(client_id, f"api://{client_id}"),
            claims={"tid": tenant_id},
        )


@dataclass(frozen=True)
class Groups:
    """Entra security groups that grant access (object IDs, lower case); none = access from app_user only."""

    user: str | None = None
    admin: str | None = None
    fallback: bool = True

    @property
    def enabled(self) -> bool:
        return bool(self.user or self.admin)


def current_user() -> str:
    return str(g.identity.username)


def admin_only[View: Callable[..., Any]](view: View) -> View:
    """Only administrators (the Admins group, or an admin app user) may call this route; others get 403, audited."""

    @wraps(view)
    def wrapped(*args, **kwargs):
        if not g.identity.is_admin:
            audit.record(
                current_app.extensions["bureau_submissions"].engine,
                current_user(),
                "ADMIN_DENIED",
                target=request.path,
                ip=request.remote_addr,
            )
            abort(403, "Only administrators can change rules and policies.")
        return view(*args, **kwargs)

    return cast(View, wrapped)


@cache
def _signing_keys(jwks_uri: str) -> jwt.PyJWKClient:
    """The provider's token signing keys, cached per process (refetched when a token names an unknown key)."""
    return jwt.PyJWKClient(jwks_uri, cache_keys=True, lifespan=3600, timeout=10)


def verify_token(token: str, config: OidcConfig) -> dict:
    """The token's claims, or abort 401 (503 when the provider's keys cannot be fetched)."""
    try:
        key = _signing_keys(config.jwks_uri).get_signing_key_from_jwt(token).key
        claims = jwt.decode(
            token,
            key,
            algorithms=list(config.algorithms),
            audience=list(config.audiences),
            issuer=list(config.issuers),
            options={"require": ["exp", "iat", "aud", "iss"]},
            leeway=60,
        )
    except jwt.PyJWKClientConnectionError:
        abort(503, "Sign-in keys could not be fetched from the sign-in provider; try again")
    except jwt.PyJWTError:
        abort(401, "Sign in again (the session token is not valid)")
    for name, value in config.claims.items():
        if claims.get(name) != value:
            abort(401, "Sign in again (token from another directory)")
    return claims


def _new_username(repo: UserRepository, email: str) -> str:
    """A free username from the e-mail's local part (a-z, 0-9, . _ -), e.g. asha.k, asha.k-2."""
    base = re.sub(r"[^a-z0-9._-]", "", email.split("@")[0].lower())[:46] or "user"
    base = base if len(base) >= 3 else (base + "-user")
    name, n = base, 1
    while repo.find(name):
        n += 1
        name = f"{base}-{n}"
    return name


def _deny(engine, email: str, claims: dict, reason: str) -> NoReturn:
    audit.record(
        engine,
        email or claims.get("oid", "-"),
        "LOGIN_DENIED",
        target=email or None,
        reason=reason,
        ip=request.remote_addr,
    )
    abort(
        403,
        f"{email or 'This account'} is not registered for Bureau Submissions, or is disabled. "
        "Ask your IT team to add you to the Bureau Submission group.",
    )


def identify(config: OidcConfig | None, groups: Groups = Groups()) -> Identity:  # noqa: B008 (frozen)
    """The caller's identity, or abort 401 (no / wrong token) or 403 (no access). `config` None = no sign-in."""
    if config is None:
        name = (request.headers.get("X-Forwarded-User") or "internal-user")[:100]
        return Identity(name, name, None, True)
    header = request.headers.get("Authorization", "")
    if not header.startswith("Bearer "):
        abort(401, "Sign in first")
    claims = verify_token(header[7:].strip(), config)
    # `email` needs the optional claim on the app registration; preferred_username / upn are the sign-in name
    email = (claims.get("email") or claims.get("preferred_username") or claims.get("upn") or "").strip().lower()
    engine = current_app.extensions["bureau_submissions"].engine
    repo = UserRepository(engine)
    user = repo.find_by_email(email)
    if user is not None and not user.is_active:
        _deny(engine, email, claims, "disabled")  # the in-app emergency stop wins over groups
    if groups.enabled:
        member_of = {str(x).lower() for x in claims.get(config.groups_claim) or []}
        overage = config.groups_claim in (
            claims.get("_claim_names") or {}
        )  # over 200 groups: Entra leaves the list out
        log_reason = "groups claim overage" if overage else "not in the groups"
        is_admin = bool(groups.admin and groups.admin in member_of)
        if is_admin or (groups.user and groups.user in member_of):
            if user is None:
                if not email:
                    _deny(engine, email, claims, "no e-mail in the token")
                name = (claims.get("name") or email.split("@")[0]).strip()
                user = repo.add(_new_username(repo, email), name, email, is_admin=is_admin)
                audit.record(
                    engine,
                    "sign-in",
                    "USER_ADDED",
                    target=user.username,
                    email=email,
                    reason="member of the sign-in group",
                    ip=request.remote_addr,
                )
            return Identity(user.username, user.display_name, user.email, is_admin)
        if user is None or not groups.fallback:
            _deny(engine, email, claims, log_reason)
        return Identity(user.username, user.display_name, user.email, user.is_admin)  # switch-over fallback
    if user is None:
        _deny(engine, email, claims, "not registered")
    return Identity(user.username, user.display_name, user.email, user.is_admin)


IdentityProvider = Callable[[], Identity]


def identity_provider(mode: str, config: OidcConfig | None = None, groups: Groups | None = None) -> IdentityProvider:
    """The sign-in check for this deployment: "entra" / "oidc" (token verified against `config`) or "none"."""
    if mode not in ("entra", "oidc", "none"):
        raise ValueError(f"Unknown sign-in mode {mode!r}: entra, oidc or none")
    if mode == "none":
        return lambda: identify(None)
    if config is None:

        def unconfigured() -> Identity:
            abort(500, "Sign-in is not configured (SUBMISSIONS_AUTH and the provider's settings)")

        return unconfigured
    return lambda: identify(config, groups or Groups())
