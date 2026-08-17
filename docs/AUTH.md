# Authentication guide

The host app sits on **both sides** of OAuth, which is the thing worth
understanding before reading any of the code:

```
   ┌──────────────────────────────────────────────────────────────┐
   │                        HOST APP                              │
   │                                                              │
   │   AS A SERVER                    AS A CLIENT                 │
   │   signs users in,                obtains tokens FROM         │
   │   issues tokens to               other MCP servers           │
   │   MCP servers that               on the user's behalf        │
   │   have no IdP                                                │
   │                                                              │
   │   /oauth/authorize               /api/connectors/*/connect   │
   │   /oauth/token                   /api/connectors/oauth/…     │
   │   /.well-known/oauth-            /.well-known/oauth-client   │
   │       authorization-server           (CIMD identity)         │
   └──────────────────────────────────────────────────────────────┘
```

Three separate flows live in this app. Keep them apart while reading:

1. **User sessions** — how a person signs into this app (§1–3)
2. **Us as an authorization server** — for MCP-2, which has no IdP (§4)
3. **Us as an OAuth client** — connecting to MCP servers (§5)

---

## 1. Signing in

```
 browser                     backend                      postgres
    │                           │                            │
    │ POST /api/auth/register   │                            │
    │──────────────────────────►│                            │
    │                           │ argon2 hash of password    │
    │                           │───────────────────────────►│ users
    │                           │                            │
    │                           │ mint access JWT (30 min)   │
    │                           │ random refresh (14 days)   │
    │                           │ store SHA-256 of refresh   │
    │                           │───────────────────────────►│ refresh_tokens
    │   Set-Cookie ×2           │                            │
    │◄──────────────────────────│                            │
```

Passwords are hashed with **argon2id** (`app/core/security.py`), which is
memory-hard and salted per hash — two hashes of the same password differ.

`authenticate_user` deliberately gives the same error for "no such user" and
"wrong password", so the endpoint cannot be used to enumerate accounts.

---

## 2. The two tokens

They are different kinds of thing, on purpose:

| | Access token | Refresh token |
|---|---|---|
| Format | JWT, signed | 48 random bytes, opaque |
| Lifetime | 30 minutes | 14 days |
| Stored server-side? | **no** | yes — as a SHA-256 hash |
| Verified by | signature check, no DB hit | database lookup |
| Cookie path | `/` | `/api/auth` |

A real access token from this app decodes to:

```json
{
  "sub":   "17b239af-0f45-4e96-89ec-3e2c8564f47e",
  "iss":   "http://localhost:8000",
  "iat":   1786945339,
  "exp":   1786947139,
  "jti":   "df41d48ebda44489bc09050572dd23bb",
  "typ":   "access",
  "scope": "chat connectors"
}
```

`typ` is checked on every decode, so a refresh token can never be presented
where an access token is required — a common and nasty confusion bug.

**Why the access token is not stored:** verifying it is a signature check, so
protected routes need no database round-trip. The cost is that it cannot be
revoked before it expires, which is why 30 minutes is short.

**Why the refresh token is stored hashed:** a database leak yields hashes, not
usable tokens — the same reasoning as password storage.

---

## 3. Cookies and rotation

### The cookies

Real `Set-Cookie` headers from `/api/auth/register`:

```
mcp_host_access=eyJhbGciOi…; HttpOnly; Max-Age=1800;    Path=/;         SameSite=lax
mcp_host_refresh=7ec-Da7cut…; HttpOnly; Max-Age=1209600; Path=/api/auth; SameSite=lax
```

Each attribute earns its place:

* **`HttpOnly`** — JavaScript cannot read these, so an XSS payload cannot
  exfiltrate the session.
* **`SameSite=lax`** — blocks CSRF on cross-site POSTs, while still allowing the
  top-level redirect back from an external OAuth provider. `strict` would break
  the connector flow.
* **`Path=/api/auth`** on the refresh cookie — it is only ever sent to the four
  auth endpoints, so it is not exposed on every API call.
* **`Secure`** — set automatically whenever `ENVIRONMENT != local`.

The frontend never touches tokens. `fetch` uses `credentials: "include"` and
the browser attaches the cookies.

### Rotation and reuse detection

Every refresh issues a **new** refresh token and revokes the old one. Replaying
a rotated token means it leaked, so the entire family is revoked:

```
   normal use                        after a token is stolen
   ──────────                        ───────────────────────
   R1 ─refresh─► R2                  R1 ─refresh─► R2   (real user)
      (R1 revoked)                   R1 ─refresh─► ✗    (attacker replays)
   R2 ─refresh─► R3                       │
      (R2 revoked)                        └─► revoke ALL tokens for the user
                                              R2 dies too → both must sign in
```

The attacker is locked out, and the legitimate user is forced to sign in again
— which is the correct outcome, because there is no way to tell which of the
two holders is genuine.

> **Implementation detail that matters.** The revocation is committed
> explicitly inside `rotate_refresh_token`, because the function then raises,
> and `get_db()` rolls back on any exception. Without that explicit commit the
> revocation is written and immediately discarded — the security response
> silently does nothing. `tests/test_auth_flow.py::test_reuse_revokes_the_whole_family`
> guards this exact regression.

---

## 4. Us as an authorization server

For MCP servers with no identity provider of their own (MCP-2, "forward auth"),
this app *is* the authorization server.

```
 MCP server's client        host app (AS)            user's browser
        │                        │                         │
        │  GET /oauth/authorize  │                         │
        │  ?client_id=…&resource=https://mcp2/mcp          │
        │  &code_challenge=…     │                         │
        │───────────────────────►│                         │
        │                        │ not signed in? ────────►│ /login
        │                        │ not trusted?  ─────────►│ /oauth/consent
        │                        │                         │
        │  302 …?code=xyz        │◄────────── approves ────│
        │◄───────────────────────│                         │
        │                        │
        │  POST /oauth/token     │
        │  code + code_verifier  │
        │───────────────────────►│ verify PKCE, single-use
        │  access + refresh JWT  │ aud = the resource
        │◄───────────────────────│
```

Discovery lives at `/.well-known/oauth-authorization-server` (RFC 8414).

Three protections, all enforced in `oauth_server_service.py`:

**PKCE is mandatory.** `code_challenge_method` must be `S256`; the endpoint
rejects a request without a challenge. This stops an intercepted code from
being redeemed by anyone but the original requester.

**Codes are single-use and short-lived.** 60 seconds, and `used_at` is stamped
on redemption. Stored as a SHA-256 hash, like refresh tokens.

**Redirect URIs match exactly.** No prefix matching — that is the classic
open-redirect token-theft hole. `test_oauth_server.py` asserts that
`…/callback/evil` and `…/callback?x=1` are both rejected.

### Audience binding (RFC 8707)

The `resource` parameter flows from the authorization request into the token's
`aud` claim. So a token minted for MCP-2 carries `aud: https://mcp2/mcp`, and
MCP-5 must reject it. Without this, one compromised MCP server could replay its
tokens against every other one.

`decode_token(token, audience=...)` performs the check; `test_security.py`
verifies that a mismatched audience raises.

---

## 5. Us as an OAuth client

When you press **Connect**, this app becomes the client:

```
 browser        host app                MCP server        its auth server
    │              │                         │                   │
    │ POST connect │                         │                   │
    │─────────────►│  1. probe for metadata  │                   │
    │              │────────────────────────►│                   │
    │              │  401 + WWW-Authenticate │                   │
    │              │◄────────────────────────│                   │
    │              │  2. GET /.well-known/oauth-protected-resource│
    │              │────────────────────────►│                   │
    │              │  3. GET AS metadata     │                   │
    │              │─────────────────────────────────────────────►
    │              │  4. identify ourselves (see below)          │
    │  auth URL    │                                             │
    │◄─────────────│                                             │
    │  ── redirect, user approves ───────────────────────────────►
    │              │                                             │
    │  /api/connectors/oauth/callback?code=…                     │
    │─────────────►│  5. exchange code + PKCE verifier ──────────►
    │              │  6. encrypt tokens → mcp_credentials        │
    │  → settings  │  7. list_tools, cache the catalogue         │
    │◄─────────────│                                             │
```

### How this app proves who it is

Four strategies, tried in order (`_obtain_client_credentials`):

| Order | Strategy | Used by |
|---|---|---|
| 1 | Static client ID | Google — requires cloud registration |
| 2 | **CIMD** | modern servers (Keycloak, Scalekit) |
| 3 | Dynamic registration (RFC 7591) | servers offering `/register` |
| 4 | CIMD URL as fallback | everything else |

**CIMD** is the interesting one. The `client_id` *is* an HTTPS URL that resolves
to a metadata document — this app's lives at `/.well-known/oauth-client`:

```json
{
  "client_id": "http://localhost:8000/.well-known/oauth-client",
  "client_name": "MCP Host",
  "redirect_uris": ["http://localhost:8000/api/connectors/oauth/callback"],
  "token_endpoint_auth_method": "none",
  "grant_types": ["authorization_code", "refresh_token"]
}
```

Any authorization server can identify this app by fetching that URL. No
pre-registration, no shared secret, no client secret to leak. When *we* accept
a CIMD client, `_resolve_cimd_client` checks that the document's `client_id`
matches the URL it was fetched from — otherwise any site could publish a
document impersonating another client.

### One redirect URI for every connector

All six MCP servers redirect to the same
`/api/connectors/oauth/callback`. The `state` parameter — a random value stored
in `oauth_transactions` alongside the PKCE verifier — is what identifies which
connection a callback belongs to. It doubles as CSRF protection: an unknown
`state` is rejected.

Transactions expire after 10 minutes.

### Token refresh

Access tokens from MCP servers expire. `MCPClientManager._get_valid_access_token`
refreshes **before** each request, with a 60-second skew so a long tool call
cannot straddle the expiry boundary.

This is also why the app runs its own tool loop instead of delegating to the
OpenAI Agents SDK's MCP client: that client takes static headers and cannot
refresh mid-run.

If refresh fails, the connection moves to `EXPIRED` and the UI prompts to
reconnect — tokens are never silently dropped.

---

## 6. How a request is authenticated

```
  request
     │
     ▼
  Authorization: Bearer …?  ──yes──► use it
     │ no
     ▼
  mcp_host_access cookie?   ──yes──► use it
     │ no
     ▼
  401 + WWW-Authenticate
```

`get_current_user` (`app/api/deps.py`) accepts either, then verifies signature,
issuer, expiry, and `typ == "access"`, and loads the user.

Routes declare it as a dependency:

```python
async def list_connectors(user: CurrentUser, db: DbSession):
```

`get_optional_user` is the variant that returns `None` instead of raising —
used by `/oauth/authorize`, which must redirect an anonymous caller to the login
page rather than return a 401.

**Ownership is checked in services, not routes.** `get_connection` verifies
`connection.user_id == user.id`, so no route can forget it.

---

## 7. Secrets

| Setting | Protects | Rotatable? |
|---|---|---|
| `JWT_SECRET_KEY` | our own tokens | yes — signs everyone out |
| `TOKEN_ENCRYPTION_KEY` | third-party OAuth tokens | **no — see below** |

> **Never rotate `TOKEN_ENCRYPTION_KEY` once tokens exist.** Every stored token
> becomes permanently undecryptable and every user must reconnect every
> connector.

Third-party tokens are encrypted with pgcrypto before they reach a column — see
[DATABASE.md](DATABASE.md#5-token-encryption).

---

## 8. Known limitation: HS256

Tokens are currently signed with **HS256**, a symmetric algorithm. So
`/.well-known/jwks.json` returns an empty key set — there is no public key to
publish.

That is fine while this app verifies its own tokens. It becomes a real problem
for MCP-2, which must verify tokens *we* issued: with HS256 the only way is to
share the signing secret, which means a compromise of MCP-2 lets an attacker
mint tokens for every user.

**Day 6 moves this to RS256** — MCP servers then verify with the public key from
JWKS, and the private key never leaves this app. It is the single highest-value
production change.

---

## 9. Testing it

```bash
cd apps/host-backend && uv run pytest -q      # 32 tests
```

Relevant files:

* `tests/test_security.py` — JWT type/audience/expiry enforcement, PKCE, argon2
* `tests/test_oauth_server.py` — exact redirect-URI matching, error redirects
* `tests/test_auth_flow.py` — live rotation, reuse detection, family revocation

Manually, against a running backend:

```bash
# sign up and keep the cookies
curl -c jar.txt -X POST localhost:8000/api/auth/register \
  -H 'Content-Type: application/json' \
  -d '{"email":"a@b.com","password":"testpass12345"}'

curl -b jar.txt localhost:8000/api/auth/me          # authenticated
curl -b jar.txt -X POST localhost:8000/api/auth/refresh   # rotates

# discovery documents
curl localhost:8000/.well-known/oauth-client
curl localhost:8000/.well-known/oauth-authorization-server
```
