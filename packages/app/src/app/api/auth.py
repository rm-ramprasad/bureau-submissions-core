"""Who is calling the API.

auth = "entra" (Azure): the browser signs in with Microsoft Entra ID (the company tenant, code + PKCE) and sends the
token as `Authorization: Bearer <token>`. App Service authentication (Easy Auth) on the Function App rejects calls
without a valid token before the app runs. The Function App endpoint is public (no network isolation), so the app
also verifies the token itself: signature (the tenant's published keys), issuer, tenant, audience (the app
registration, as client id or api://<client id>) and expiry.

Who may use the app:
- With Entra security groups configured (SUBMISSIONS_ENTRA_USER_GROUP_ID / _ADMIN_GROUP_ID; the app registration emits
  the `groups` claim): members of either group get in, Admins group members are administrators. IT manages access in
  Entra. Their app_user row is created at first sign-in (name, e-mail, last sign-in); an app_user disabled in the app
  stays locked out (emergency stop). While SUBMISSIONS_APP_USER_FALLBACK is on (the switch-over), active app users
  outside the groups still get in, with their app_user admin flag.
- Without groups: the signed-in e-mail must belong to an active app_user (who may use the app, and who is an
  administrator).

auth = "none" (local development and tests): no sign-in; a fronting proxy may pass X-Forwarded-User.

The shell picks one with `identity_provider(...)` and hands it to create_app: the port is any callable that returns
the caller's Identity for the current request (or aborts 401 / 403). Generic OIDC (AWS) comes in step 3.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from functools import cache, wraps

import jwt
from flask import abort, current_app, g, request

from app import audit
from app.users import UserRepository

LOGIN = "https://login.microsoftonline.com"


@dataclass(frozen=True)
class Identity:
    username: str  # recorded on requests, exceptions and the audit trail
    display_name: str
    email: str | None
    is_admin: bool


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
    return g.identity.username


def admin_only(view):
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

    return wrapped


@cache
def _signing_keys(tenant_id: str) -> jwt.PyJWKClient:
    """The tenant's token signing keys, cached per process (refetched when a token names an unknown key)."""
    return jwt.PyJWKClient(f"{LOGIN}/{tenant_id}/discovery/v2.0/keys", cache_keys=True, lifespan=3600, timeout=10)


def verify_token(token: str, tenant_id: str, client_id: str) -> dict:
    """The token's claims, or abort 401. Accepts v2 (ID or access token) and v1 access tokens of this tenant."""
    try:
        key = _signing_keys(tenant_id).get_signing_key_from_jwt(token).key
        claims = jwt.decode(
            token,
            key,
            algorithms=["RS256"],
            audience=[client_id, f"api://{client_id}"],
            issuer=[f"{LOGIN}/{tenant_id}/v2.0", f"https://sts.windows.net/{tenant_id}/"],
            options={"require": ["exp", "iat", "aud", "iss"]},
            leeway=60,
        )
    except jwt.PyJWKClientConnectionError:
        abort(503, "Sign-in keys could not be fetched from Microsoft Entra ID; try again")
    except jwt.PyJWTError:
        abort(401, "Sign in again (the session token is not valid)")
    if claims.get("tid") != tenant_id:
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


def _deny(engine, email: str, claims: dict, reason: str):
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


def identify(auth_mode: str, tenant_id: str | None, client_id: str | None, groups: Groups = Groups()) -> Identity:  # noqa: B008 (frozen)
    """The caller's identity, or abort 401 (no / wrong token) or 403 (no access)."""
    if auth_mode == "none":
        name = (request.headers.get("X-Forwarded-User") or "internal-user")[:100]
        return Identity(name, name, None, True)
    if auth_mode != "entra" or not tenant_id or not client_id:
        abort(
            500,
            "Sign-in is not configured (SUBMISSIONS_AUTH, SUBMISSIONS_ENTRA_TENANT_ID, SUBMISSIONS_ENTRA_CLIENT_ID)",
        )
    header = request.headers.get("Authorization", "")
    if not header.startswith("Bearer "):
        abort(401, "Sign in first")
    claims = verify_token(header[7:].strip(), tenant_id, client_id)
    # `email` needs the optional claim on the app registration; preferred_username / upn are the sign-in name
    email = (claims.get("email") or claims.get("preferred_username") or claims.get("upn") or "").strip().lower()
    engine = current_app.extensions["bureau_submissions"].engine
    repo = UserRepository(engine)
    user = repo.find_by_email(email)
    if user is not None and not user.is_active:
        _deny(engine, email, claims, "disabled")  # the in-app emergency stop wins over groups
    if groups.enabled:
        member_of = {str(x).lower() for x in claims.get("groups") or []}
        overage = "groups" in (claims.get("_claim_names") or {})  # over 200 groups: Entra leaves the list out
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
                    reason="member of the Entra group",
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


def identity_provider(
    mode: str, tenant_id: str | None = None, client_id: str | None = None, groups: Groups | None = None
) -> IdentityProvider:
    """The sign-in check for this deployment: "entra" (token verified in the app) or "none" (development, tests)."""
    if mode not in ("entra", "none"):
        raise ValueError(f"Unknown sign-in mode {mode!r}: entra or none")
    return lambda: identify(mode, tenant_id, client_id, groups or Groups())
