import base64
import hashlib
import html
import json
import logging
import os
import re
import secrets
import time
from dataclasses import dataclass
from urllib.parse import parse_qs, quote, urlencode, urlsplit, urlunsplit

from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse, Response

from app.store import connect

log = logging.getLogger("company-brain.oauth")

ACCESS_PREFIX = "cb_at_"
REFRESH_PREFIX = "cb_rt_"
CODE_PREFIX = "cb_cd_"
CLIENT_PREFIX = "cb_cl_"
STABLE_REDIRECT = "https://chatgpt.com/connector_platform_oauth_redirect"
CLAUDE_REDIRECTS = frozenset(
    {
        "https://claude.ai/api/mcp/auth_callback",
        "https://claude.com/api/mcp/auth_callback",
    }
)
SCOPE = "search"
_CALLBACK_REDIRECT = re.compile(r"https://chatgpt\.com/connector/oauth/[A-Za-z0-9_-]{1,128}\Z")
_CLIENT_ID = re.compile(r"cb_cl_[A-Za-z0-9_-]{20,80}\Z")
_CHALLENGE = re.compile(r"[A-Za-z0-9_-]{43,128}\Z")
_VERIFIER = re.compile(r"[A-Za-z0-9\-._~]{43,128}\Z")
_HOST = re.compile(r"[A-Za-z0-9.-]+(?::\d{1,5})?\Z")
_ORIGIN = re.compile(r"https?://[A-Za-z0-9.-]+(?::\d{1,5})?\Z")
_EMAIL = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+\Z")
_FIELDS = (
    "response_type",
    "client_id",
    "redirect_uri",
    "code_challenge",
    "code_challenge_method",
    "state",
    "scope",
    "resource",
)
_ERROR_TEXT = {
    "invalid_request": "The request is missing a required parameter.",
    "invalid_grant": "The code or refresh token is not valid.",
    "invalid_scope": "The only scope is search.",
    "unsupported_response_type": "Only code is supported.",
    "access_denied": "The request was denied.",
    "invalid_client": "The client is not valid.",
    "unsupported_grant_type": "That grant is not supported.",
    "invalid_target": "The resource is not valid.",
    "invalid_redirect_uri": "The redirect URI is not allowed.",
    "invalid_client_metadata": "The client metadata is not valid.",
}
_DUMMY = None
_PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__</title>
<style>
body { font-family: sans-serif; margin: 2rem auto; max-width: 28rem; line-height: 1.45; }
label { display: block; margin-top: 0.8rem; }
input { width: 100%; font: inherit; padding: 0.45rem; box-sizing: border-box; }
button { font: inherit; margin: 1rem 0.6rem 0 0; padding: 0.45rem 0.8rem; }
</style>
</head>
<body>
<main>
__BODY__
</main>
</body>
</html>
"""


def enabled() -> bool:
    return os.environ.get("OAUTH_ENABLED", "").strip().lower() in {"1", "true", "yes", "on"}


def redirect_allowed(uri: str) -> bool:
    if uri == STABLE_REDIRECT or uri in CLAUDE_REDIRECTS:
        return True
    return _CALLBACK_REDIRECT.fullmatch(uri) is not None


def pkce_s256(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    n, r, p = 2**14, 8, 1
    digest = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=n, r=r, p=p, dklen=32)
    return f"scrypt${n}${r}${p}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        kind, n, r, p, salt_hex, digest_hex = stored.split("$")
        if kind != "scrypt":
            return False
        n_int, r_int, p_int = int(n), int(r), int(p)
        expected = bytes.fromhex(digest_hex)
        if n_int != 2**14 or r_int != 8 or p_int != 1 or len(expected) != 32:
            return False
        got = hashlib.scrypt(
            password.encode("utf-8"),
            salt=bytes.fromhex(salt_hex),
            n=n_int,
            r=r_int,
            p=p_int,
            dklen=32,
        )
    except Exception:
        return False
    return secrets.compare_digest(got, expected)


def external_origin(headers: dict[str, str], scheme: str) -> str | None:
    configured = os.environ.get("PUBLIC_BASE_URL", "").strip().rstrip("/")
    if configured:
        return configured if _ORIGIN.fullmatch(configured) else None
    proto = (headers.get("x-forwarded-proto") or scheme or "").split(",")[0].strip().lower()
    host = (headers.get("x-forwarded-host") or headers.get("host") or "").split(",")[0].strip()
    if host.endswith(".onrender.com"):
        proto = "https"
    if proto not in {"http", "https"} or not _HOST.fullmatch(host):
        return None
    origin = f"{proto}://{host}"
    if not _ORIGIN.fullmatch(origin):
        return None
    return origin


def www_authenticate(origin: str) -> str:
    return (
        'Bearer resource_metadata="'
        + origin
        + '/.well-known/oauth-protected-resource", scope="search"'
    )


def audience_ok(resource: str, origin: str) -> bool:
    return bool(origin) and resource in {origin, origin + "/mcp"}


def ensure_bootstrap(conn) -> bool:
    email = os.environ.get("OAUTH_BOOTSTRAP_EMAIL", "").strip().lower()
    password = os.environ.get("OAUTH_BOOTSTRAP_PASSWORD", "")
    if not _email_ok(email) or not password or len(password) > 1024:
        log.error("OAuth is on, but the bootstrap email or password is missing.")
        return False
    try:
        with conn.transaction():
            row = conn.execute(
                "SELECT id, email, password_hash FROM oauth_users WHERE bootstrap"
            ).fetchone()
            if row is None:
                conn.execute(
                    """
                    INSERT INTO oauth_users (email, password_hash, bootstrap)
                    VALUES (%s, %s, true)
                    """,
                    (email, hash_password(password)),
                )
                log.info("OAuth bootstrap user is ready")
            else:
                user_id, current, stored = row
                if current != email:
                    conn.execute(
                        "UPDATE oauth_users SET email = %s WHERE id = %s",
                        (email, user_id),
                    )
                if not verify_password(password, stored):
                    conn.execute(
                        "UPDATE oauth_users SET password_hash = %s WHERE id = %s",
                        (hash_password(password), user_id),
                    )
                    log.info("OAuth bootstrap password was updated")
        conn.commit()
    except Exception:
        log.exception("OAuth bootstrap user was not saved")
        conn.rollback()
        return False
    return True


class BearerGuard:
    def __init__(self, app, key: str):
        self.app = app
        self.key = key or ""

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and scope.get("path", "").startswith("/mcp"):
            headers = _scope_headers(scope)
            if not _request_authorized(headers, self.key, scope):
                origin = external_origin(headers, scope.get("scheme") or "http")
                extra = {"Cache-Control": "no-store"}
                if enabled() and origin:
                    extra["WWW-Authenticate"] = www_authenticate(origin)
                await JSONResponse({"error": "unauthorized"}, status_code=401, headers=extra)(
                    scope, receive, send
                )
                return
        await self.app(scope, receive, send)


def protected_resource_document(origin: str) -> dict:
    return {
        "resource": origin,
        "authorization_servers": [origin],
        "scopes_supported": [SCOPE],
        "bearer_methods_supported": ["header"],
        "resource_name": "Company Brain",
    }


def authorization_server_document(origin: str) -> dict:
    return {
        "issuer": origin,
        "authorization_endpoint": origin + "/oauth/authorize",
        "token_endpoint": origin + "/oauth/token",
        "registration_endpoint": origin + "/oauth/register",
        "revocation_endpoint": origin + "/oauth/revoke",
        "response_types_supported": ["code"],
        "grant_types_supported": ["authorization_code", "refresh_token"],
        "code_challenge_methods_supported": ["S256"],
        "token_endpoint_auth_methods_supported": ["none"],
        "scopes_supported": [SCOPE],
        "authorization_response_iss_parameter_supported": True,
    }


def registration_problem(data: dict) -> str | None:
    uris = data.get("redirect_uris")
    if not isinstance(uris, list) or not uris or not all(isinstance(item, str) for item in uris):
        return "invalid_redirect_uri"
    cleaned = []
    for uri in uris:
        if uri not in cleaned:
            cleaned.append(uri)
    if len(cleaned) > 4 or any(not redirect_allowed(uri) for uri in cleaned):
        return "invalid_redirect_uri"
    if data.get("token_endpoint_auth_method", "none") != "none":
        return "invalid_client_metadata"
    grants = data.get("grant_types", ["authorization_code"])
    allowed_grants = {"authorization_code", "refresh_token"}
    if (
        not isinstance(grants, list)
        or not grants
        or any(grant not in allowed_grants for grant in grants)
    ):
        return "invalid_client_metadata"
    if data.get("response_types", ["code"]) != ["code"]:
        return "invalid_client_metadata"
    scope = data.get("scope")
    if scope is not None and (not isinstance(scope, str) or not _scope_ok(scope)):
        return "invalid_client_metadata"
    return None


def register_client(conn, data: dict) -> dict:
    problem = registration_problem(data)
    if problem:
        return {"error": problem}
    cleaned = []
    for uri in data["redirect_uris"]:
        if uri not in cleaned:
            cleaned.append(uri)
    client_id = CLIENT_PREFIX + secrets.token_urlsafe(24)
    name = _client_name(data.get("client_name"))
    conn.execute(
        "INSERT INTO oauth_clients (id, redirect_uris, client_name) VALUES (%s, %s, %s)",
        (client_id, json.dumps(cleaned), name),
    )
    issued = {
        "client_id": client_id,
        "client_id_issued_at": int(time.time()),
        "redirect_uris": cleaned,
        "token_endpoint_auth_method": "none",
        "grant_types": ["authorization_code", "refresh_token"],
        "response_types": ["code"],
        "scope": SCOPE,
    }
    if name:
        issued["client_name"] = name
    log.info("OAuth client registered")
    return issued


def exchange_code(conn, origin: str, form: dict) -> dict:
    code = form.get("code", "")
    client_id = form.get("client_id", "")
    redirect_uri = form.get("redirect_uri", "")
    verifier = form.get("code_verifier", "")
    resource = form.get("resource") or None
    if form.get("client_secret"):
        return {"error": "invalid_client"}
    if not code or not client_id or not redirect_uri or not verifier:
        return {"error": "invalid_request"}
    if len(code) > 200 or not _VERIFIER.fullmatch(verifier):
        return {"error": "invalid_grant"}
    with conn.transaction():
        row = conn.execute(
            """
            UPDATE oauth_codes
            SET used_at = now()
            WHERE code_hash = %s
              AND used_at IS NULL
              AND expires_at > now()
            RETURNING client_id, redirect_uri, code_challenge, scope, resource, user_id
            """,
            (_hash(code),),
        ).fetchone()
        if row is None:
            return {"error": "invalid_grant"}
        stored_client, stored_redirect, challenge, scope, stored_resource, user_id = row
        if stored_client != client_id or stored_redirect != redirect_uri:
            return {"error": "invalid_grant"}
        if resource and resource != stored_resource:
            return {"error": "invalid_grant"}
        if scope != SCOPE or not audience_ok(stored_resource, origin):
            return {"error": "invalid_grant"}
        if not _pkce_match(verifier, challenge):
            return {"error": "invalid_grant"}
        pair = _issue_pair(conn, user_id, stored_client, scope, stored_resource)
    log.info("OAuth access token issued")
    return pair


def rotate_refresh(conn, origin: str, form: dict) -> dict:
    token = form.get("refresh_token", "")
    client_id = form.get("client_id", "")
    resource = form.get("resource") or None
    if form.get("client_secret"):
        return {"error": "invalid_client"}
    if not token or not client_id:
        return {"error": "invalid_request"}
    if not token.startswith(REFRESH_PREFIX) or len(token) > 200:
        return {"error": "invalid_grant"}
    digest = _hash(token)
    with conn.transaction():
        row = conn.execute(
            """
            UPDATE oauth_tokens
            SET revoked_at = now()
            WHERE token_hash = %s
              AND kind = 'refresh'
              AND revoked_at IS NULL
              AND expires_at > now()
            RETURNING user_id, client_id, scope, resource
            """,
            (digest,),
        ).fetchone()
        if row is None:
            stale = conn.execute(
                """
                SELECT user_id, client_id
                FROM oauth_tokens
                WHERE token_hash = %s AND kind = 'refresh' AND revoked_at IS NOT NULL
                """,
                (digest,),
            ).fetchone()
            if stale:
                conn.execute(
                    """
                    UPDATE oauth_tokens
                    SET revoked_at = now()
                    WHERE user_id = %s AND client_id = %s AND revoked_at IS NULL
                    """,
                    (stale[0], stale[1]),
                )
            return {"error": "invalid_grant"}
        user_id, stored_client, scope, stored_resource = row
        if stored_client != client_id or (resource and resource != stored_resource):
            return {"error": "invalid_grant"}
        if scope != SCOPE or not audience_ok(stored_resource, origin):
            return {"error": "invalid_grant"}
        pair = _issue_pair(conn, user_id, stored_client, scope, stored_resource)
    log.info("OAuth access token issued")
    return pair


def revoke_token(conn, token: str, client_id: str) -> None:
    if not token or len(token) > 200:
        return
    if not (token.startswith(ACCESS_PREFIX) or token.startswith(REFRESH_PREFIX)):
        return
    with conn.transaction():
        row = conn.execute(
            """
            SELECT kind, user_id, client_id, revoked_at
            FROM oauth_tokens
            WHERE token_hash = %s
            """,
            (_hash(token),),
        ).fetchone()
        if row is None:
            return
        kind, user_id, stored_client, revoked_at = row
        if client_id and client_id != stored_client:
            return
        if revoked_at is None:
            conn.execute(
                """
                UPDATE oauth_tokens
                SET revoked_at = now()
                WHERE token_hash = %s AND revoked_at IS NULL
                """,
                (_hash(token),),
            )
        other = "refresh" if kind == "access" else "access"
        conn.execute(
            """
            UPDATE oauth_tokens
            SET revoked_at = now()
            WHERE user_id = %s AND client_id = %s AND kind = %s AND revoked_at IS NULL
            """,
            (user_id, stored_client, other),
        )


def token_allows(conn, token: str, origin: str) -> bool:
    if not token.startswith(ACCESS_PREFIX) or len(token) > 200 or not origin:
        return False
    row = conn.execute(
        """
        SELECT resource, scope
        FROM oauth_tokens
        WHERE token_hash = %s
          AND kind = 'access'
          AND revoked_at IS NULL
          AND expires_at > now()
        """,
        (_hash(token),),
    ).fetchone()
    if row is None:
        return False
    resource, scope = row
    return scope == SCOPE and audience_ok(resource, origin)


@dataclass
class _AuthSpec:
    client_id: str
    redirect_uri: str
    challenge: str
    state: str | None
    resource: str
    scope: str
    client_name: str | None
    params: dict


def resolve_authorize(conn, params: dict, origin: str) -> _AuthSpec | Response:
    picked = _picked(params)
    if picked is None:
        return _invalid_link()
    client_id = picked.get("client_id", "")
    redirect_uri = picked.get("redirect_uri", "")
    row = None
    if _CLIENT_ID.fullmatch(client_id):
        row = conn.execute(
            "SELECT redirect_uris, client_name FROM oauth_clients WHERE id = %s",
            (client_id,),
        ).fetchone()
    registered = _load_uris(row[0]) if row else []
    if redirect_uri not in registered or not redirect_allowed(redirect_uri):
        log.info("Rejected authorize request")
        return _invalid_link()
    state = _clean_state(picked)
    if state is False:
        return _redirect_error(redirect_uri, origin, "invalid_request", None)
    response_type = picked.get("response_type", "")
    if response_type != "code":
        error = "unsupported_response_type" if response_type else "invalid_request"
        return _redirect_error(redirect_uri, origin, error, state)
    challenge = picked.get("code_challenge", "").rstrip("=")
    if picked.get("code_challenge_method") != "S256" or not _CHALLENGE.fullmatch(challenge):
        return _redirect_error(redirect_uri, origin, "invalid_request", state)
    if not _scope_ok(picked.get("scope")):
        return _redirect_error(redirect_uri, origin, "invalid_scope", state)
    resource = picked.get("resource") or origin
    if resource not in {origin, origin + "/mcp"}:
        return _redirect_error(redirect_uri, origin, "invalid_target", state)
    return _AuthSpec(
        client_id=client_id,
        redirect_uri=redirect_uri,
        challenge=challenge,
        state=state,
        resource=resource,
        scope=SCOPE,
        client_name=row[1],
        params=picked,
    )


def issue_code(conn, spec: _AuthSpec, user_id: int) -> str:
    raw = CODE_PREFIX + secrets.token_urlsafe(32)
    conn.execute(
        """
        INSERT INTO oauth_codes (
            code_hash, client_id, user_id, redirect_uri, code_challenge,
            scope, resource, expires_at
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, now() + interval '1 minute')
        """,
        (
            _hash(raw),
            spec.client_id,
            user_id,
            spec.redirect_uri,
            spec.challenge,
            spec.scope,
            spec.resource,
        ),
    )
    log.info("OAuth code issued")
    return raw


async def protected_resource(request: Request) -> Response:
    origin = _request_origin(request)
    if origin is None:
        return _oauth_error("invalid_request")
    return _json(protected_resource_document(origin))


async def authorization_server(request: Request) -> Response:
    origin = _request_origin(request)
    if origin is None:
        return _oauth_error("invalid_request")
    return _json(authorization_server_document(origin))


async def register(request: Request) -> Response:
    data = await _json_object(request)
    if data is None:
        return _oauth_error("invalid_request")
    problem = registration_problem(data)
    if problem:
        return _oauth_error(problem)
    with connect() as conn:
        result = register_client(conn, data)
    return _json(result, 201)


async def authorize(request: Request) -> Response:
    origin = _request_origin(request)
    if origin is None:
        return _invalid_link()
    params = _query(request) if request.method == "GET" else await _string_form(request)
    if params is None:
        return _invalid_link()
    with connect() as conn:
        outcome = resolve_authorize(conn, params, origin)
        if isinstance(outcome, Response):
            return outcome
        user_id = _session_user(conn, request.cookies.get("cb_consent", ""))
        if request.method == "POST":
            decision = params.get("decision", "")
            if user_id and decision == "deny":
                return _redirect_error(outcome.redirect_uri, origin, "access_denied", outcome.state)
            if user_id and decision == "allow":
                code = issue_code(conn, outcome, user_id)
                return _redirect_success(outcome, origin, code)
            if decision in {"allow", "deny"}:
                return _login_page(outcome, "Sign in again.", 400)
            return _consent_page(outcome) if user_id else _login_page(outcome, "", 200)
        if user_id:
            return _consent_page(outcome)
        return _login_page(outcome, "", 200)


async def login(request: Request) -> Response:
    origin = _request_origin(request)
    if origin is None:
        return _invalid_link()
    params = await _string_form(request)
    if params is None:
        return _invalid_link()
    with connect() as conn:
        outcome = resolve_authorize(conn, params, origin)
        if isinstance(outcome, Response):
            return outcome
        user_id = authenticate(conn, params.get("email", ""), params.get("password", ""))
        if user_id is None:
            log.info("OAuth sign-in failed")
            return _login_page(outcome, "Email or password is wrong.", 400)
        raw = _create_session(conn, user_id)
    response = RedirectResponse(_authorize_url(origin, outcome.params), status_code=303)
    response.headers["Cache-Control"] = "no-store"
    response.set_cookie(
        "cb_consent",
        raw,
        max_age=600,
        httponly=True,
        secure=origin.startswith("https://"),
        samesite="lax",
        path="/oauth",
    )
    return response


async def token(request: Request) -> Response:
    origin = _request_origin(request)
    form = await _token_fields(request)
    if origin is None or form is None:
        return _oauth_error("invalid_request")
    grant = form.get("grant_type", "").strip()
    with connect() as conn:
        if grant == "authorization_code":
            result = exchange_code(conn, origin, form)
        elif grant == "refresh_token":
            result = rotate_refresh(conn, origin, form)
        elif grant:
            result = {"error": "unsupported_grant_type"}
        else:
            result = {"error": "invalid_request"}
    if "error" in result:
        return _oauth_error(result["error"])
    return _json(result)


async def revoke(request: Request) -> Response:
    form = await _token_fields(request)
    if form and form.get("token"):
        with connect() as conn:
            revoke_token(conn, form.get("token", ""), form.get("client_id", ""))
    return Response(status_code=200, headers=_public_headers())


async def preflight(_request: Request) -> Response:
    return Response(status_code=204, headers=_public_headers())


ENDPOINTS = (
    ("/.well-known/oauth-protected-resource", ["GET"], protected_resource),
    ("/.well-known/oauth-protected-resource/mcp", ["GET"], protected_resource),
    ("/.well-known/oauth-authorization-server", ["GET"], authorization_server),
    ("/oauth/register", ["POST"], register),
    ("/oauth/authorize", ["GET", "POST"], authorize),
    ("/oauth/login", ["POST"], login),
    ("/oauth/token", ["POST"], token),
    ("/oauth/revoke", ["POST"], revoke),
)
OPTIONS = (
    "/.well-known/oauth-protected-resource",
    "/.well-known/oauth-protected-resource/mcp",
    "/.well-known/oauth-authorization-server",
    "/oauth/register",
    "/oauth/token",
    "/oauth/revoke",
)


def register_routes(server) -> None:
    if not enabled():
        return
    for path, methods, handler in ENDPOINTS:
        server.custom_route(path, methods=methods)(handler)
    for path in OPTIONS:
        server.custom_route(path, methods=["OPTIONS"])(preflight)


def starlette_routes():
    from starlette.routing import Route

    routes = [Route(path, endpoint=handler, methods=methods) for path, methods, handler in ENDPOINTS]
    routes.extend(Route(path, endpoint=preflight, methods=["OPTIONS"]) for path in OPTIONS)
    return routes


def authenticate(conn, email: str, password: str) -> int | None:
    if not isinstance(email, str) or not isinstance(password, str):
        return None
    email = email.strip().lower()
    if not _email_ok(email) or not password or len(password) > 1024:
        verify_password("x", _dummy_hash())
        return None
    row = conn.execute(
        "SELECT id, password_hash FROM oauth_users WHERE email = %s",
        (email,),
    ).fetchone()
    if row is None or not verify_password(password, row[1]):
        if row is None:
            verify_password(password, _dummy_hash())
        return None
    return int(row[0])


def _issue_pair(conn, user_id: int, client_id: str, scope: str, resource: str) -> dict:
    access = ACCESS_PREFIX + secrets.token_urlsafe(32)
    refresh = REFRESH_PREFIX + secrets.token_urlsafe(32)
    conn.execute(
        """
        INSERT INTO oauth_tokens (
            token_hash, kind, client_id, user_id, scope, resource, expires_at
        )
        VALUES
            (%s, 'access', %s, %s, %s, %s, now() + interval '1 hour'),
            (%s, 'refresh', %s, %s, %s, %s, now() + interval '30 days')
        """,
        (
            _hash(access),
            client_id,
            user_id,
            scope,
            resource,
            _hash(refresh),
            client_id,
            user_id,
            scope,
            resource,
        ),
    )
    return {
        "access_token": access,
        "token_type": "Bearer",
        "expires_in": 3600,
        "refresh_token": refresh,
        "scope": scope,
    }


def _create_session(conn, user_id: int) -> str:
    raw = secrets.token_urlsafe(32)
    conn.execute(
        """
        INSERT INTO oauth_sessions (token_hash, user_id, expires_at)
        VALUES (%s, %s, now() + interval '10 minutes')
        """,
        (_hash(raw), user_id),
    )
    return raw


def _session_user(conn, raw: str) -> int | None:
    if not raw or len(raw) > 200:
        return None
    row = conn.execute(
        """
        SELECT user_id FROM oauth_sessions
        WHERE token_hash = %s AND expires_at > now()
        """,
        (_hash(raw),),
    ).fetchone()
    return int(row[0]) if row else None


def _request_authorized(headers: dict[str, str], key: str, scope) -> bool:
    got = headers.get("authorization", "")
    if key and _same(got, f"Bearer {key}"):
        return True
    if not enabled():
        return False
    token = _bearer_value(got)
    if not token:
        return False
    origin = external_origin(headers, scope.get("scheme") or "http")
    if not origin:
        return False
    try:
        with connect() as conn:
            return token_allows(conn, token, origin)
    except Exception:
        log.exception("OAuth token check failed")
        return False


def _scope_headers(scope) -> dict[str, str]:
    headers = {}
    for name, value in scope.get("headers") or []:
        headers[name.decode("latin-1").lower()] = value.decode("latin-1")
    return headers


def _bearer_value(header: str) -> str | None:
    if not header.lower().startswith("bearer "):
        return None
    token = header[7:]
    if not token or token != token.strip() or any(ch.isspace() for ch in token):
        return None
    return token


def _same(left: str, right: str) -> bool:
    try:
        a = left.encode("latin-1")
        b = right.encode("latin-1")
    except UnicodeEncodeError:
        return False
    if len(a) != len(b):
        return False
    return secrets.compare_digest(a, b)


def _request_origin(request: Request) -> str | None:
    headers = {key.lower(): value for key, value in request.headers.items()}
    return external_origin(headers, request.url.scheme)


def _query(request: Request) -> dict[str, str]:
    return {key: request.query_params.get(key, "") for key in request.query_params.keys()}


async def _string_form(request: Request) -> dict[str, str] | None:
    try:
        form = await request.form()
    except Exception:
        return None
    data = {}
    for key in form.keys():
        value = form.get(key)
        if isinstance(value, str):
            data[key] = value
    return data


async def _json_object(request: Request) -> dict | None:
    raw = await _read_body(request)
    if raw is None:
        return None
    try:
        data = json.loads(raw)
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    return data


async def _token_fields(request: Request) -> dict[str, str] | None:
    raw = await _read_body(request)
    if raw is None:
        return None
    text = raw.decode("utf-8", errors="replace")
    if text.lstrip().startswith("{"):
        try:
            data = json.loads(text)
        except Exception:
            return None
        if not isinstance(data, dict):
            return None
        return {str(key): value for key, value in data.items() if isinstance(value, str)}
    parsed = parse_qs(text, keep_blank_values=True)
    return {key: values[-1] for key, values in parsed.items() if values and isinstance(values[-1], str)}


async def _read_body(request: Request) -> bytes | None:
    raw = await request.body()
    if len(raw) > 65536:
        return None
    return raw


def _picked(params: dict) -> dict[str, str] | None:
    if not isinstance(params, dict):
        return None
    picked = {}
    for key in _FIELDS:
        if key not in params:
            continue
        value = params[key]
        if not isinstance(value, str) or len(value) > 4000:
            return None
        picked[key] = value
    return picked


def _clean_state(params: dict) -> str | None | bool:
    if "state" not in params:
        return None
    state = params.get("state")
    if not isinstance(state, str) or len(state) > 2048 or any(ord(ch) < 32 for ch in state):
        return False
    return state


def _load_uris(stored: str) -> list:
    try:
        uris = json.loads(stored)
    except Exception:
        return []
    if not isinstance(uris, list):
        return []
    return [uri for uri in uris if isinstance(uri, str)]


def _scope_ok(scope: str | None) -> bool:
    if scope is None or scope.strip() == "":
        return True
    return scope.split() == [SCOPE]


def _pkce_match(verifier: str, challenge: str) -> bool:
    try:
        got = pkce_s256(verifier)
    except Exception:
        return False
    if len(got) != len(challenge):
        return False
    return secrets.compare_digest(got, challenge)


def _redirect_success(spec: _AuthSpec, origin: str, code: str) -> RedirectResponse:
    return _oauth_redirect(spec.redirect_uri, origin, code=code, state=spec.state)


def _redirect_error(redirect_uri: str, origin: str, error: str, state: str | None) -> RedirectResponse:
    return _oauth_redirect(
        redirect_uri,
        origin,
        error=error,
        error_description=_ERROR_TEXT[error],
        state=state,
    )


def _oauth_redirect(redirect_uri: str, issuer: str, **params) -> RedirectResponse:
    if not redirect_allowed(redirect_uri):
        raise RuntimeError("blocked redirect")
    query = {key: value for key, value in params.items() if value is not None}
    query["iss"] = issuer
    parts = urlsplit(redirect_uri)
    location = urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query, quote_via=quote), ""))
    return RedirectResponse(
        location,
        status_code=302,
        headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"},
    )


def _authorize_url(origin: str, params: dict) -> str:
    pairs = [(key, params[key]) for key in _FIELDS if params.get(key)]
    return origin + "/oauth/authorize?" + urlencode(pairs, quote_via=quote)


def _login_page(spec: _AuthSpec, message: str, status: int) -> HTMLResponse:
    notice = f"<p>{html.escape(message)}</p>" if message else ""
    body = (
        "<h1>Sign in</h1>"
        "<p>Sign in to let this connection search the company folder.</p>"
        + notice
        + '<form method="post" action="/oauth/login">'
        + _hidden(spec.params)
        + '<label for="email">Email</label>'
        + '<input id="email" name="email" type="email" autocomplete="username" required>'
        + '<label for="password">Password</label>'
        + '<input id="password" name="password" type="password" autocomplete="current-password" required>'
        + '<button type="submit">Sign in</button>'
        + "</form>"
    )
    return _html(status, "Sign in", body)


def _consent_page(spec: _AuthSpec) -> HTMLResponse:
    who = html.escape(spec.client_name) if spec.client_name else "This connection"
    body = (
        "<h1>Allow search</h1>"
        + f"<p>{who} is asking to search the company folder.</p>"
        + "<p>After you allow it, the browser returns to "
        + html.escape(spec.redirect_uri)
        + ".</p>"
        + '<form method="post" action="/oauth/authorize">'
        + _hidden(spec.params)
        + '<button type="submit" name="decision" value="allow">Allow</button>'
        + '<button type="submit" name="decision" value="deny">Deny</button>'
        + "</form>"
    )
    return _html(200, "Allow search", body)


def _invalid_link() -> HTMLResponse:
    return _html(400, "Sign in", "<h1>Sign in</h1><p>This sign-in link is not valid.</p>")


def _hidden(params: dict) -> str:
    parts = []
    for key in _FIELDS:
        if not params.get(key):
            continue
        value = html.escape(params[key], quote=True)
        parts.append(f'<input type="hidden" name="{key}" value="{value}">')
    return "".join(parts)


def _html(status: int, title: str, body: str) -> HTMLResponse:
    page = _PAGE.replace("__TITLE__", html.escape(title)).replace("__BODY__", body)
    return HTMLResponse(page, status_code=status, headers={"Cache-Control": "no-store"})


def _json(payload: dict, status: int = 200) -> JSONResponse:
    return JSONResponse(payload, status_code=status, headers=_public_headers())


def _oauth_error(error: str) -> JSONResponse:
    return _json({"error": error, "error_description": _ERROR_TEXT.get(error, error)}, 400)


def _public_headers() -> dict[str, str]:
    return {
        "Cache-Control": "no-store",
        "Pragma": "no-cache",
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Headers": "Content-Type, Authorization",
        "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
    }


def _client_name(value) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = "".join(ch for ch in value.strip() if ch.isprintable())
    cleaned = cleaned[:120]
    return cleaned or None


def _email_ok(email: str) -> bool:
    return bool(email) and len(email) <= 320 and _EMAIL.fullmatch(email) is not None


def _dummy_hash() -> str:
    global _DUMMY
    if _DUMMY is None:
        _DUMMY = hash_password("not-a-real-password")
    return _DUMMY


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()
