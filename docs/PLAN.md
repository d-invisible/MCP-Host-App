# Six-day build plan

Day 1 is done. This is the sequence for the rest, chosen so that each day ends
with something you can actually click on, and so the hardest auth work happens
only after the easy cases have proven the plumbing.

---

## Day 1 — Host app ✅ complete

Backend, auth server, database, and UI. See the root `README.md`.

**Verified working:** register/login, connector catalogue, connect to a live MCP
server over Streamable HTTP, tool discovery + caching, enable/disable/disconnect,
streaming chat with a full tool-calling loop, encrypted token storage.

---

## Day 2 — The two easy MCP servers

Build these first: they exercise the host end-to-end without any OAuth
complexity, so any bug you hit is a *transport* bug, not an auth bug.

**MCP-3 — no auth** (`apps/mcp-servers/public-tools`, port 8103)
- Tools: `current_time`, `convert_units`, `hash_text`
- No `AuthSettings`; the host connects and syncs tools immediately.

**MCP-1 — its own authorization server** (`apps/mcp-servers/notes`, port 8101)
- A small FastAPI auth server on **8001** with `/.well-known/oauth-authorization-server`,
  `/authorize`, `/token`, `/register`, plus its own user table.
- The MCP server publishes `/.well-known/oauth-protected-resource/mcp` and
  verifies bearer tokens with a `TokenVerifier`, checking the `aud` claim
  against its own resource URL.
- Tools: `list_notes`, `create_note`, `search_notes`.

**Done when:** connecting to Notes bounces you through its login, lands back on
`/settings/connectors`, and its tools appear in chat.

> Key API shapes (verified against the installed SDK v2):
> ```python
> from mcp.server import MCPServer                      # not FastMCP
> from mcp.server.auth.provider import AccessToken, TokenVerifier
> from mcp.server.auth.settings import AuthSettings
>
> mcp = MCPServer("Notes", token_verifier=V(), auth=AuthSettings(
>     issuer_url=..., resource_server_url=..., required_scopes=[...]))
> mcp.run(transport="streamable-http", host=..., port=...)
> ```

---

## Day 3 — Forwarded auth and decentralized identity

**MCP-2 — forwards auth to this app** (port 8102)
- No auth server of its own: its protected-resource metadata points
  `authorization_servers` at the host app's issuer.
- Its `TokenVerifier` validates the host's JWT and **must** check that `aud`
  equals its own resource URL — this is what the RFC 8707 `resource` parameter
  already flowing through `connector_service` is for.
- Tools: `list_tasks`, `create_task`, `complete_task`.

**MCP-5 — Keycloak (or Scalekit)** (port 8105)
- Add Keycloak to `infra/docker-compose.yml`; create a realm and enable dynamic
  client registration.
- Verify tokens against the realm's JWKS.
- This is where CIMD gets its real test: if the provider supports it, the host
  authenticates as `http://localhost:8000/.well-known/oauth-client` with no
  registration step at all.

**Done when:** four connectors work, each with a different auth mechanism.

---

## Day 4 — Google

Left until now because it is the only one needing an external account, and its
constraints (no dynamic registration, exact redirect URIs) are unlike the rest.

**Manual setup you must do:**
1. Google Cloud console → new project → OAuth consent screen (External, Testing).
2. Create an **OAuth client ID** (Web application).
3. Authorized redirect URI: `http://localhost:8000/api/connectors/oauth/callback`
4. Put the client ID in the connector's `static_client_id`, and the secret in
   `.env` as `GOOGLE_CLIENT_SECRET`.

**MCP-4 (port 8104)** — Google sign-in only; tools read the verified identity.
**Google Workspace (port 8106)** — real Calendar/Gmail read-only calls.

Google issues opaque access tokens, so the server validates via `tokeninfo`
rather than JWKS. Refresh tokens only arrive with `access_type=offline` and
`prompt=consent`, which is exactly the path the existing refresh logic needs.

---

## Day 5 — Deploy, free tier

| Piece | Host | Notes |
|---|---|---|
| Postgres | **Neon** free | pgcrypto is available |
| Backend + MCP servers | **Fly.io** or **Render** free | one app each, or one app with several processes |
| Frontend | **Vercel** free | set `NEXT_PUBLIC_API_BASE_URL` |

Things that must change when you leave localhost:
- `BACKEND_BASE_URL` / `FRONTEND_BASE_URL` → real https URLs.
- Cookies become `Secure` automatically (`environment != local`); for
  cross-site cookies you will need `SameSite=None`.
- Add the deployed callback to the Google client's redirect URIs.
- Generate **fresh** `TOKEN_ENCRYPTION_KEY` and `JWT_SECRET_KEY`, and store them
  as platform secrets. Changing the encryption key invalidates stored tokens.

---

## Day 6 — Hardening

- **RS256 + real JWKS.** Day 1 signs with HS256, so `/.well-known/jwks.json` is
  empty by design. Moving to RS256 lets MCP-2 verify tokens without sharing the
  secret. This is the single most valuable production change.
- Rate limits on `/api/auth/*` and `/oauth/token`.
- An audit log for connector connect/disconnect and every tool call.
- A cleanup job for expired `oauth_transactions` and `authorization_codes`.
- Playwright E2E: sign up → connect → chat with a tool.
- Per-tool consent for destructive tools (the `require_approval` idea).

---

## Ordering rationale

- **No-auth first** proves transport before auth.
- **Own-auth-server second** gives a provider you fully control, so a failure is
  never ambiguous.
- **Forwarded auth third**, once the host's own token issuance is trusted.
- **Google last**, because it is the least forgiving and the only one that can
  block you on an external console.
