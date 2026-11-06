"""Generic OIDC sign-in against a mock issuer: a local HTTP server publishing its keys (JWKS), as Cognito or Keycloak
would. The app fetches the real key set over HTTP, so this covers key lookup as well as the claim checks."""

from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from app.api.auth import ADMIN_ROLES, ROLES, USER_ROLES, Identity, OidcConfig, _signing_keys
from app.users import UserRepository

AUDIENCE = "bsub-web"
GROUPS_CLAIM = "cognito:groups"


class MockIssuer:
    def __init__(self) -> None:
        self.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        public = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(self.key.public_key()))
        jwks = json.dumps({"keys": [public | {"kid": "mock-1", "use": "sig", "alg": "RS256"}]}).encode()

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                if self.path != "/.well-known/jwks.json":
                    self.send_error(404)
                    return
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(jwks)

            def log_message(self, *args) -> None:
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def config(self) -> OidcConfig:
        return OidcConfig(
            issuers=(self.url,),
            jwks_uri=f"{self.url}/.well-known/jwks.json",
            audiences=(AUDIENCE,),
            groups_claim=GROUPS_CLAIM,
        )

    def token(self, key=None, kid="mock-1", **claims) -> str:
        now = int(time.time())
        claims = {"iss": self.url, "aud": AUDIENCE, "iat": now, "exp": now + 3600, "email": "ravi@nbfc.in"} | claims
        payload = {k: v for k, v in claims.items() if v is not None}
        return jwt.encode(payload, key or self.key, algorithm="RS256", headers={"kid": kid})


@pytest.fixture
def issuer():
    mock = MockIssuer()
    yield mock
    mock.server.shutdown()
    _signing_keys.cache_clear()


@pytest.fixture
def oidc_api(make_api, issuer):
    def make(**groups):  # entra_user_group_id / entra_admin_group_id / app_user_fallback: the make_api names
        return make_api(auth="oidc", oidc=issuer.config(), **groups)

    return make


def bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def test_tokens_are_checked_against_the_issuers_published_keys(oidc_api, issuer, engine):
    UserRepository(engine).add("ravi", "Ravi", "ravi@nbfc.in", is_admin=False)
    api = oidc_api()
    status, me = api.get("/me", headers=bearer(issuer.token()))
    assert status == 200 and me["username"] == "ravi" and me["is_admin"] is False
    other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    assert api.get("/me", headers=bearer(issuer.token(key=other)))[0] == 401  # not the issuer's key
    assert api.get("/me", headers=bearer(issuer.token(kid="unknown")))[0] == 401  # key not published
    assert api.get("/me", headers=bearer(issuer.token(aud="someone-else")))[0] == 401
    assert api.get("/me", headers=bearer(issuer.token(iss="https://evil.example")))[0] == 401
    assert api.get("/me", headers=bearer(issuer.token(exp=1)))[0] == 401
    assert api.get("/me", headers=bearer(issuer.token(email="stranger@nbfc.in")))[0] == 403  # not registered


def test_groups_come_from_the_configured_claim(oidc_api, issuer):
    api = oidc_api(entra_user_group_id="bsub-users", entra_admin_group_id="bsub-admins", app_user_fallback=False)
    status, me = api.get("/me", headers=bearer(issuer.token(**{GROUPS_CLAIM: ["bsub-admins"]})))
    assert status == 200 and me["is_admin"] is True  # added at first sign-in
    status, me = api.get("/me", headers=bearer(issuer.token(email="u@nbfc.in", **{GROUPS_CLAIM: ["bsub-users"]})))
    assert status == 200 and me["is_admin"] is False
    status, _ = api.get("/me", headers=bearer(issuer.token(email="x@nbfc.in", groups=["bsub-users"])))
    assert status == 403  # Entra's claim name is not the one configured


def test_unreachable_keys_are_a_503(make_api, engine):
    config = OidcConfig(issuers=("http://127.0.0.1:9",), jwks_uri="http://127.0.0.1:9/jwks", audiences=("a",))
    api = make_api(auth="oidc", oidc=config)
    token = jwt.encode({"iss": "x"}, "secret-key-of-sufficient-length-1234", headers={"kid": "k"})
    assert api.get("/me", headers=bearer(token))[0] == 503
    _signing_keys.cache_clear()


def test_oidc_without_settings_is_a_500(make_api):
    assert make_api(auth="oidc").get("/me")[0] == 500


def test_entra_is_one_oidc_configuration():
    c = OidcConfig.entra("t-1", "c-1")
    assert c.issuers == ("https://login.microsoftonline.com/t-1/v2.0", "https://sts.windows.net/t-1/")
    assert c.jwks_uri == "https://login.microsoftonline.com/t-1/discovery/v2.0/keys"
    assert (c.audiences, c.groups_claim, dict(c.claims)) == (("c-1", "api://c-1"), "groups", {"tid": "t-1"})


def test_roles_for_maker_checker_are_defined():
    assert ROLES == ("viewer", "maker", "approver", "admin", "auditor")
    assert Identity("a", "A", None, is_admin=True).roles == ADMIN_ROLES == frozenset(ROLES)
    assert Identity("u", "U", None, is_admin=False).roles == USER_ROLES == {"viewer", "maker", "approver"}
