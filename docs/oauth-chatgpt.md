# ChatGPT OAuth

Plan only. No code yet. Claude keeps the bearer key. ChatGPT is advertised only after the proof at the bottom passes on the live test service.

ChatGPT's connector has two choices: OAuth, or no authentication. It has nowhere to put `MCP_API_KEY`. Taking the bearer check off `/mcp` would publish the folder. This plan adds OAuth on the same container so a person can sign in and ChatGPT can hold a short-lived token.

The folder stays one shared index. A signed-in person can search all of it. OAuth does not copy each person's Google Drive permissions. That matches the bearer key today.

## Done when

All six are true on `https://company-brain-4f8c.onrender.com`:

1. `/mcp` with no header returns 401 and a `WWW-Authenticate` header that points at the protected-resource metadata.
2. `/mcp` with `Authorization: Bearer <MCP_API_KEY>` still searches.
3. A ChatGPT connector, after the bootstrap user signs in, runs `search` and gets a sample passage back.
4. The same connector with the token revoked gets 401 and no passage.
5. An authorize or register request whose redirect is not a ChatGPT connector URL is rejected.
6. Replaying an authorization code is rejected.

Until then the event line stays "Ask Claude". Do not say ChatGPT, and do not say the sign-in is secure.

## Decisions

| Topic | Decision |
| --- | --- |
| Where it runs | In this process, on the same host as `/mcp`. No second bill. The login rows sit in the Postgres volume the company already pays for. |
| What stays | `BearerGuard` on `/mcp`. An empty `MCP_API_KEY` still fails closed. Claude's header is unchanged. |
| What a token is | Opaque, stored as a hash. Checked for expiry, scope, and audience. Revocable. Not a JWT, and not the API key. |
| Who may sign in | One bootstrap user, email plus password, created from Render secrets. A second user is a later row. There is no public signup. |
| What they may call | One scope, `search`. It covers `search`, `list_sources`, and `feedback`. No tool is anonymous. |
| Client registration | Dynamic client registration only, for the first proof. Redirect URIs are allowlisted. CIMD and `private_key_jwt` wait until this proof is green. |
| WorkOS | Still later. Company-email SSO is a different job. This server has to speak the MCP discovery and PKCE bits either way. |
| Switch | `OAUTH_ENABLED` unset means today's server. Metadata, login, and token routes are not mounted. |

## What ChatGPT has to see

OpenAI's connector auth is OAuth 2.1 authorization-code with PKCE `S256`, plus the MCP discovery docs. The server side of that, on this host:

| Route | Who can call it | What it returns |
| --- | --- | --- |
| `GET /.well-known/oauth-protected-resource` | Anyone | `resource` is `https://<host>` with no path and no trailing slash. `authorization_servers` is that same origin. `scopes_supported` is `search`. |
| `GET /.well-known/oauth-authorization-server` | Anyone | Issuer is that same origin. Authorization and token URLs. `code_challenge_methods_supported` is `S256`. `registration_endpoint` is present. Token auth method is `none`. No OpenID scopes. |
| `POST /oauth/register` | ChatGPT | A `client_id` only when every redirect URI is on the allowlist. |
| `GET /oauth/authorize` | The person, in a browser | Login, then consent, then a one-time code to the allowlisted redirect. |
| `POST /oauth/token` | ChatGPT | An access token and a refresh token, after the code and the PKCE check. |

`/mcp` without a valid Claude key or a valid access token returns:

```http
HTTP/1.1 401 Unauthorized
WWW-Authenticate: Bearer resource_metadata="https://<host>/.well-known/oauth-protected-resource", scope="search"
```

`/health` stays open and stays unauthenticated. The well-known and `/oauth/` routes stay outside `BearerGuard`. Everything else on `/mcp` stays closed.

ChatGPT echoes `resource` on the authorize and token requests. The token row stores that exact string. A token minted for another audience is rejected. If the live connector sends `https://<host>/mcp` instead of the origin, the stored audience becomes that exact string and the metadata is changed to match. Nothing else moves.

Redirect allowlist, exact strings:

- `https://chatgpt.com/connector_platform_oauth_redirect`
- The callback URL shown on that connector's page, once it exists, in the form `https://chatgpt.com/connector/oauth/<callback_id>`

No other redirect. No wildcard.

The authorization response includes `iss`, and the metadata says `authorization_response_iss_parameter_supported: true`, so ChatGPT can use the stable redirect. `iss` is the issuer string, character for character.

## Login and tokens

The bootstrap email and password are Render secrets, separate from `MCP_API_KEY`. The password is stored with `hashlib.scrypt`. The consent cookie is `Secure`, `HttpOnly`, and `SameSite=Lax`.

Codes live for one minute, are single use, and are stored as a hash with the PKCE challenge. The token endpoint requires `S256`. A missing or wrong verifier, or a reused code, returns an error and no token.

Access tokens last one hour. Refresh tokens rotate: the old refresh row is revoked when the new pair is written. A restart reads the rows from Postgres, so a deploy does not invent a second login. Logout and a failed proof revoke the rows.

The access token prefix is `cb_at_`. The API key never uses that prefix. Logs may show the prefix. They do not show the token, the code, the verifier, or the password.

`search` is the only advertised scope. `openid`, `email`, and `profile` are not advertised, so ChatGPT is not asked for an identity this server does not return.

## Rejected on purpose

- Removing the bearer check so ChatGPT can connect.
- Putting `MCP_API_KEY` in the OAuth client or in the consent page.
- A shared workshop password in the place of the bootstrap user. The password stays a Render secret. The people in the room do not take a copy.
- Anonymous tools, client-credentials, or an API key grant. ChatGPT cannot use those.
- Per-file permissions. The signed-in user sees the whole folder, same as the bearer key.
- A second hosted identity provider in this slice.

## Build order

1. Tables for users, clients, codes, and tokens. Bootstrap user from the two Render secrets. `OAUTH_ENABLED` gates the routes.
2. Metadata and the 401 challenge. Prove with curl that `/mcp` is still closed and that Claude's bearer still searches.
3. Register, authorize, token, refresh, revoke. Prove the redirect allowlist, PKCE, replay, expiry, and audience checks with curl.
4. Add the connector on the test service. Sign in as the bootstrap user. `search` returns a sample passage. Revoke the token and confirm the next call is 401.
5. Only then change the event line.

If step 4 fails because ChatGPT does not offer the sign-in button from the 401 header alone, the tool error also carries the same challenge. That is a follow-up on this plan, not a reason to open `/mcp`.

## Advertise

The Saturday copy can add one line after the proof:

"You can also connect ChatGPT. You sign in. The folder is not public."

Before the proof, that line is false.
