import base64
import hashlib
import os
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit

import psycopg
import pytest
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.testclient import TestClient

from app.oauth import (
    CLAUDE_REDIRECTS,
    BearerGuard,
    audience_ok,
    enabled,
    ensure_bootstrap,
    external_origin,
    hash_password,
    pkce_s256,
    redirect_allowed,
    register_routes,
    starlette_routes,
    verify_password,
    www_authenticate,
)
from app.store import _migration_statements, _statements

STABLE = "https://chatgpt.com/connector_platform_oauth_redirect"
CALLBACK = "https://chatgpt.com/connector/oauth/callback123"
VERIFIER = "a" * 64
EMAIL = "owner@company-brain.local"
PASSWORD = "test-password-123"
ORIGIN = "https://brain.test"


class _Routes:
    def __init__(self):
        self.paths = []

    def custom_route(self, path, methods):
        def decorate(func):
            self.paths.append(path)
            return func

        return decorate


def _challenge(verifier=VERIFIER):
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def _browser():
    app = Starlette(routes=starlette_routes())
    return TestClient(app, base_url=ORIGIN, follow_redirects=False)


def _guard(key):
    async def ok(scope, receive, send):
        await JSONResponse({"ok": True, "path": scope.get("path")})(scope, receive, send)

    return TestClient(BearerGuard(ok, key), base_url=ORIGIN, follow_redirects=False)


def _params(client_id, **extra):
    params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": STABLE,
        "code_challenge": _challenge(),
        "code_challenge_method": "S256",
        "state": "xyz",
        "scope": "search",
    }
    params.update(extra)
    return params


def _register(client, redirect_uris=None, name="ChatGPT"):
    response = client.post(
        "/oauth/register",
        json={
            "redirect_uris": redirect_uris or [STABLE],
            "token_endpoint_auth_method": "none",
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"],
            "client_name": name,
            "scope": "search",
        },
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["token_endpoint_auth_method"] == "none"
    assert body["redirect_uris"] == (redirect_uris or [STABLE])
    return body["client_id"]


def _location_query(response):
    location = response.headers["location"]
    parts = urlsplit(location)
    return location, parts, dict(parse_qsl(parts.query))


def _allow(client, client_id, resource=None):
    params = _params(client_id, **({"resource": resource} if resource else {}))
    page = client.get("/oauth/authorize", params=params)
    assert page.status_code == 200
    assert "super-secret-key" not in page.text
    login = client.post(
        "/oauth/login",
        data={**params, "email": EMAIL, "password": PASSWORD},
    )
    assert login.status_code == 303
    cookie = login.headers["set-cookie"].lower()
    assert "cb_consent=" in cookie
    assert "httponly" in cookie
    assert "secure" in cookie
    assert "samesite=lax" in cookie
    assert PASSWORD not in cookie
    consent_url = urlsplit(login.headers["location"])
    consent = client.get(consent_url.path + "?" + consent_url.query)
    assert consent.status_code == 200
    assert "Allow" in consent.text
    allowed = client.post("/oauth/authorize", data={**params, "decision": "allow"})
    assert allowed.status_code == 302
    location, parts, query = _location_query(allowed)
    assert location.startswith(STABLE + "?")
    assert parts.netloc == "chatgpt.com"
    assert query["iss"] == ORIGIN
    assert query["state"] == "xyz"
    assert query["code"].startswith("cb_cd_")
    assert "error" not in query
    return query["code"], params


def _exchange(client, client_id, code, verifier=VERIFIER, resource=None):
    form = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": STABLE,
        "client_id": client_id,
        "code_verifier": verifier,
    }
    if resource:
        form["resource"] = resource
    return client.post("/oauth/token", data=form)


@pytest.fixture()
def browser():
    with _browser() as client:
        yield client


def _apply_oauth(connection):
    script = (Path(__file__).resolve().parent.parent / "migrations" / "003_oauth.sql").read_text()
    for statement in _statements(script):
        connection.execute(statement)
    connection.commit()


@pytest.fixture()
def oauth_conn(monkeypatch):
    url = os.environ.get("TEST_DATABASE_URL", "").strip()
    if not url:
        pytest.skip("TEST_DATABASE_URL is not set")
    monkeypatch.setenv("DATABASE_URL", url)
    monkeypatch.setenv("OAUTH_ENABLED", "1")
    monkeypatch.delenv("PUBLIC_BASE_URL", raising=False)
    monkeypatch.setenv("OAUTH_BOOTSTRAP_EMAIL", EMAIL)
    monkeypatch.setenv("OAUTH_BOOTSTRAP_PASSWORD", PASSWORD)
    monkeypatch.setenv("MCP_API_KEY", "super-secret-key")
    with psycopg.connect(url) as connection:
        _apply_oauth(connection)
        connection.execute(
            "TRUNCATE oauth_tokens, oauth_codes, oauth_sessions, oauth_clients, oauth_users "
            "RESTART IDENTITY CASCADE"
        )
        connection.commit()
        assert ensure_bootstrap(connection)
        yield connection


@pytest.fixture()
def signed_in(oauth_conn):
    with _browser() as client:
        yield client


@pytest.mark.parametrize(
    "uri",
    [
        "https://evil.example/callback",
        "https://chatgpt.com.evil/connector_platform_oauth_redirect",
        "http://chatgpt.com/connector_platform_oauth_redirect",
        STABLE + "/extra",
        STABLE + "?next=https://evil.example",
        "https://chatgpt.com/connector/oauth/abc/def",
        "https://chatgpt.com/connector/oauth/abc?x=1",
        "https://chatgpt.com/connector/oauth/../abc",
        "https://user:pass@chatgpt.com/connector_platform_oauth_redirect",
        STABLE + " ",
        "https://chatgpt.com/connector/oauth/",
        "https://claude.ai.evil/api/mcp/auth_callback",
        "http://claude.ai/api/mcp/auth_callback",
        "https://claude.ai/api/mcp/auth_callback/extra",
        "https://claude.ai/api/mcp/auth_callback?next=1",
        "https://user:pass@claude.ai/api/mcp/auth_callback",
        "https://claude.com/api/mcp/auth_callback/extra",
        "http://localhost/callback",
        "http://127.0.0.1:3118/callback",
    ],
)
def test_redirect_allowlist(uri):
    assert not redirect_allowed(uri)


def test_chatgpt_redirects_are_allowed():
    assert redirect_allowed(STABLE)
    assert redirect_allowed(CALLBACK)
    assert redirect_allowed("https://chatgpt.com/connector/oauth/Ab_cd-12")


def test_claude_redirects_are_the_two_exact_callbacks():
    assert CLAUDE_REDIRECTS == {
        "https://claude.ai/api/mcp/auth_callback",
        "https://claude.com/api/mcp/auth_callback",
    }
    for uri in CLAUDE_REDIRECTS:
        assert redirect_allowed(uri)


def test_register_rejects_a_redirect_outside_the_allowlist(browser):
    response = browser.post(
        "/oauth/register",
        json={"redirect_uris": [STABLE, "https://evil.example/callback"]},
    )
    assert response.status_code == 400
    assert response.json()["error"] == "invalid_redirect_uri"
    assert "location" not in response.headers


def test_register_rejects_a_confidential_client(browser):
    response = browser.post(
        "/oauth/register",
        json={
            "redirect_uris": [STABLE],
            "token_endpoint_auth_method": "client_secret_basic",
        },
    )
    assert response.status_code == 400
    assert response.json()["error"] == "invalid_client_metadata"


def test_pkce_s256_is_sha256_without_padding():
    assert pkce_s256(VERIFIER) == _challenge()
    assert pkce_s256(VERIFIER) != _challenge("b" * 64)


def test_password_hash_is_scrypt():
    stored = hash_password("correct horse")
    assert stored.startswith("scrypt$16384$8$1$")
    assert verify_password("correct horse", stored)
    assert not verify_password("wrong", stored)
    assert not verify_password("correct horse", "scrypt$14$8$1$00$00")


def test_metadata_advertises_search_and_pkce(browser):
    resource = browser.get("/.well-known/oauth-protected-resource")
    alias = browser.get("/.well-known/oauth-protected-resource/mcp")
    server = browser.get("/.well-known/oauth-authorization-server")
    assert resource.status_code == 200
    assert resource.json() == alias.json()
    assert resource.json()["resource"] == ORIGIN
    assert resource.json()["authorization_servers"] == [ORIGIN]
    assert resource.json()["scopes_supported"] == ["search"]
    body = server.json()
    assert body["issuer"] == ORIGIN
    assert body["authorization_endpoint"] == ORIGIN + "/oauth/authorize"
    assert body["token_endpoint"] == ORIGIN + "/oauth/token"
    assert body["registration_endpoint"] == ORIGIN + "/oauth/register"
    assert body["code_challenge_methods_supported"] == ["S256"]
    assert body["token_endpoint_auth_methods_supported"] == ["none"]
    assert body["scopes_supported"] == ["search"]
    assert body["authorization_response_iss_parameter_supported"] is True
    assert body["grant_types_supported"] == ["authorization_code", "refresh_token"]
    text = resource.text + server.text
    assert "openid" not in text
    assert "client_secret" not in text


def test_public_base_url_is_the_issuer(browser, monkeypatch):
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://files.example/")
    body = browser.get("/.well-known/oauth-authorization-server").json()
    assert body["issuer"] == "https://files.example"
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://files.example/mcp")
    assert browser.get("/.well-known/oauth-protected-resource").status_code == 400


def test_render_host_stays_https():
    assert external_origin({"host": "company-brain-4f8c.onrender.com"}, "http") == (
        "https://company-brain-4f8c.onrender.com"
    )
    assert external_origin({"host": "bad host"}, "https") is None
    assert audience_ok(ORIGIN, ORIGIN)
    assert audience_ok(ORIGIN + "/mcp", ORIGIN)
    assert not audience_ok("https://evil.example", ORIGIN)


def test_options_are_public(browser):
    response = browser.options("/oauth/token")
    assert response.status_code == 204
    assert response.headers["access-control-allow-origin"] == "*"


def test_oauth_routes_stay_unmounted_until_enabled(monkeypatch):
    monkeypatch.delenv("OAUTH_ENABLED", raising=False)
    assert not enabled()
    server = _Routes()
    register_routes(server)
    assert server.paths == []
    monkeypatch.setenv("OAUTH_ENABLED", "1")
    register_routes(server)
    assert "/.well-known/oauth-protected-resource" in server.paths
    assert "/oauth/token" in server.paths
    assert "/oauth/authorize" in server.paths


def test_bearer_key_still_opens_mcp_and_a_missing_key_stays_closed(monkeypatch):
    monkeypatch.delenv("OAUTH_ENABLED", raising=False)

    def boom():
        raise AssertionError("db")

    monkeypatch.setattr("app.oauth.connect", boom)
    closed = _guard("secret")
    missing = closed.get("/mcp")
    assert missing.status_code == 401
    assert missing.json() == {"error": "unauthorized"}
    assert "www-authenticate" not in missing.headers
    assert closed.get("/health").status_code == 200
    assert closed.get("/mcp", headers={"Authorization": "Bearer secret"}).status_code == 200
    assert closed.get("/mcp", headers={"Cookie": "cb_consent=cb_at_nope"}).status_code == 401
    empty = _guard("")
    assert empty.get("/mcp", headers={"Authorization": "Bearer secret"}).status_code == 401


def test_oauth_challenge_points_at_the_metadata(monkeypatch):
    monkeypatch.setenv("OAUTH_ENABLED", "1")
    monkeypatch.delenv("PUBLIC_BASE_URL", raising=False)
    denied = _guard("secret").get("/mcp")
    assert denied.status_code == 401
    assert denied.headers["www-authenticate"] == www_authenticate(ORIGIN)
    assert denied.json() == {"error": "unauthorized"}


def test_oauth_migration_is_part_of_startup():
    joined = "\n".join(_migration_statements())
    assert "CREATE TABLE IF NOT EXISTS oauth_users" in joined
    assert "CREATE TABLE IF NOT EXISTS oauth_tokens" in joined


def test_schema_and_sign_in_flow(signed_in, oauth_conn):
    _apply_oauth(oauth_conn)
    client_id = _register(signed_in, [CALLBACK, STABLE], name="<script>alert(1)</script>")
    code, params = _allow(signed_in, client_id)
    page = signed_in.get("/oauth/authorize", params=params)
    assert "<script>" not in page.text
    assert "&lt;script&gt;" in page.text
    first = _exchange(signed_in, client_id, code)
    body = first.json()
    assert first.status_code == 200
    assert body["token_type"] == "Bearer"
    assert body["expires_in"] == 3600
    assert body["scope"] == "search"
    assert body["access_token"].startswith("cb_at_")
    assert body["refresh_token"].startswith("cb_rt_")
    replay = _exchange(signed_in, client_id, code)
    assert replay.status_code == 400
    assert replay.json()["error"] == "invalid_grant"
    assert "access_token" not in replay.json()
    guard = _guard("claude-key")
    assert guard.get(
        "/mcp", headers={"Authorization": "Bearer " + body["access_token"]}
    ).status_code == 200
    assert guard.get(
        "/mcp", headers={"Authorization": "Bearer " + body["refresh_token"]}
    ).status_code == 401
    revoked = signed_in.post(
        "/oauth/revoke",
        data={"token": body["access_token"], "client_id": client_id},
    )
    assert revoked.status_code == 200
    assert guard.get(
        "/mcp", headers={"Authorization": "Bearer " + body["access_token"]}
    ).status_code == 401


def test_wrong_verifier_burns_the_code(signed_in):
    client_id = _register(signed_in)
    code, _params = _allow(signed_in, client_id)
    bad = _exchange(signed_in, client_id, code, verifier="b" * 64)
    good = _exchange(signed_in, client_id, code)
    assert bad.json()["error"] == "invalid_grant"
    assert good.json()["error"] == "invalid_grant"
    assert "access_token" not in bad.json()
    assert "access_token" not in good.json()


def test_refresh_reuse_revokes_the_new_tokens(signed_in):
    client_id = _register(signed_in)
    code, _params = _allow(signed_in, client_id)
    first = _exchange(signed_in, client_id, code).json()
    refreshed = signed_in.post(
        "/oauth/token",
        data={
            "grant_type": "refresh_token",
            "refresh_token": first["refresh_token"],
            "client_id": client_id,
        },
    )
    second = refreshed.json()
    assert refreshed.status_code == 200
    assert second["access_token"] != first["access_token"]
    guard = _guard("claude-key")
    assert guard.get(
        "/mcp", headers={"Authorization": "Bearer " + second["access_token"]}
    ).status_code == 200
    reused = signed_in.post(
        "/oauth/token",
        data={
            "grant_type": "refresh_token",
            "refresh_token": first["refresh_token"],
            "client_id": client_id,
        },
    )
    assert reused.json()["error"] == "invalid_grant"
    assert guard.get(
        "/mcp", headers={"Authorization": "Bearer " + second["access_token"]}
    ).status_code == 401
    again = signed_in.post(
        "/oauth/token",
        data={
            "grant_type": "refresh_token",
            "refresh_token": second["refresh_token"],
            "client_id": client_id,
        },
    )
    assert again.json()["error"] == "invalid_grant"


def test_audience_must_match_the_code_and_this_host(signed_in, oauth_conn):
    client_id = _register(signed_in)
    code, _params = _allow(signed_in, client_id, resource=ORIGIN + "/mcp")
    mismatched = _exchange(signed_in, client_id, code, resource=ORIGIN)
    assert mismatched.json()["error"] == "invalid_grant"
    code, _params = _allow(signed_in, client_id, resource=ORIGIN + "/mcp")
    matched = _exchange(signed_in, client_id, code, resource=ORIGIN + "/mcp")
    token = matched.json()["access_token"]
    guard = _guard("claude-key")
    assert guard.get("/mcp", headers={"Authorization": "Bearer " + token}).status_code == 200
    oauth_conn.rollback()
    oauth_conn.execute(
        "UPDATE oauth_tokens SET resource = 'https://evil.example' WHERE kind = 'access'"
    )
    oauth_conn.commit()
    assert guard.get("/mcp", headers={"Authorization": "Bearer " + token}).status_code == 401


def test_bad_scope_and_bad_redirect_do_not_leave_the_allowlist(signed_in):
    client_id = _register(signed_in)
    scoped = signed_in.get("/oauth/authorize", params=_params(client_id, scope="openid"))
    location, parts, query = _location_query(scoped)
    assert scoped.status_code == 302
    assert location.startswith(STABLE + "?")
    assert parts.netloc == "chatgpt.com"
    assert query["error"] == "invalid_scope"
    assert query["iss"] == ORIGIN
    assert "code" not in query
    evil = signed_in.get(
        "/oauth/authorize",
        params=_params(client_id, redirect_uri="https://evil.example/callback"),
    )
    assert evil.status_code == 400
    assert "location" not in evil.headers
    assert "evil.example" not in evil.text


def test_denied_consent_returns_to_chatgpt_without_a_code(signed_in):
    client_id = _register(signed_in)
    params = _params(client_id)
    login = signed_in.post(
        "/oauth/login",
        data={**params, "email": EMAIL, "password": PASSWORD},
    )
    assert login.status_code == 303
    denied = signed_in.post("/oauth/authorize", data={**params, "decision": "deny"})
    _location, _parts, query = _location_query(denied)
    assert query["error"] == "access_denied"
    assert query["iss"] == ORIGIN
    assert query["state"] == "xyz"
    assert "code" not in query


def test_wrong_password_sets_no_cookie(signed_in):
    client_id = _register(signed_in)
    response = signed_in.post(
        "/oauth/login",
        data={**_params(client_id), "email": EMAIL, "password": "not-the-password"},
    )
    assert response.status_code == 400
    assert "set-cookie" not in response.headers
    assert "not-the-password" not in response.text
    assert "wrong" in response.text.lower()


def test_expired_code_is_rejected(signed_in, oauth_conn):
    client_id = _register(signed_in)
    code, _params = _allow(signed_in, client_id)
    oauth_conn.rollback()
    oauth_conn.execute("UPDATE oauth_codes SET expires_at = now() - interval '1 minute'")
    oauth_conn.commit()
    response = _exchange(signed_in, client_id, code)
    assert response.json()["error"] == "invalid_grant"
    assert "access_token" not in response.json()


def test_bootstrap_password_can_be_rotated(signed_in, oauth_conn, monkeypatch):
    monkeypatch.setenv("OAUTH_BOOTSTRAP_PASSWORD", "replacement-password-456")
    assert ensure_bootstrap(oauth_conn)
    client_id = _register(signed_in)
    params = _params(client_id)
    old = signed_in.post(
        "/oauth/login",
        data={**params, "email": EMAIL, "password": PASSWORD},
    )
    assert old.status_code == 400
    new = signed_in.post(
        "/oauth/login",
        data={**params, "email": EMAIL, "password": "replacement-password-456"},
    )
    assert new.status_code == 303
