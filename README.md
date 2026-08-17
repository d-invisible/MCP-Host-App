# MCP Host Web App

A production-shaped MCP **host**: a chat interface backed by an LLM that can call
tools from any number of connected MCP servers, each with its own authorization
model. Connectors can be enabled, disabled, disconnected, or deleted per user,
and every third-party token is encrypted at rest.

> **Status:** Day 1 complete — host backend, auth server, and web UI.
> The six demo MCP servers land on Days 2–4.

---

## Stack

| Layer    | Choice |
|----------|--------|
| Frontend | Next.js 16 (App Router), TypeScript, Tailwind v4, shadcn/ui, TanStack Query v5 |
| Backend  | Python 3.13, FastAPI, uv, Pydantic v2, SQLAlchemy 2 (async), Alembic |
| Database | PostgreSQL 17 + `pgcrypto` |
| AI       | OpenAI Responses API (also Azure AI Foundry) |
| MCP      | MCP Python SDK v2, Streamable HTTP |

---

## Quick start

```bash
# 1. Database
cd infra && docker compose up -d

# 2. Backend  (http://localhost:8000)
cd apps/host-backend
uv sync
uv run alembic upgrade head        # required — the app will not start without it
uv run uvicorn app.main:app --reload --port 8000

# 3. Frontend (http://localhost:3001)
cd apps/web
npm install
npm run dev -- --port 3001
```

Open <http://localhost:3001>, create an account, and go to **Connectors**.

### Configuration you must supply

Copy `.env.example` to `.env`. The secrets are pre-generated for local use; the
only value you need to add is an LLM key:

```bash
OPENAI_API_KEY=sk-...
OPENAI_MODEL=gpt-5
```

For **Azure AI Foundry** instead — these take precedence when set:

```bash
AZURE_AI_FOUNDRY_BASE_URL=https://<resource>.services.ai.azure.com/openai/v1
AZURE_AI_FOUNDRY_API_KEY=...
AZURE_AI_FOUNDRY_DEPLOYMENT=gpt-4.1-mini   # deployment name, not model name
```

Foundry's `/openai/v1` surface is OpenAI-compatible and takes the key as a
bearer token, so no `api-version` is needed. `GET /health` reports which
provider is active.

Without a key everything still runs; chat replies with a clear error instead of
a response.

---

## How it fits together

```
Browser ──► Next.js ──► FastAPI host
                          ├── /api/auth/*          sessions (HttpOnly cookies)
                          ├── /oauth/*             our OAuth 2.1 server
                          ├── /.well-known/*       discovery + CIMD
                          ├── /api/connectors/*    connect / enable / disable
                          └── /api/chat/*          streaming chat (SSE)
                                   │
                                   ├── OpenAI Responses API
                                   └── MCP clients ──► MCP servers
```

### The two OAuth roles

> Full details — sessions, token rotation, PKCE, CIMD, and audience binding —
> are in **[docs/AUTH.md](docs/AUTH.md)**.

This app sits on both sides of OAuth, which is the part worth understanding:

1. **As an authorization server** — it issues tokens for MCP servers that have no
   identity provider of their own (the "forward auth" case, MCP-2). Metadata is
   published at `/.well-known/oauth-authorization-server`.

2. **As an OAuth client** — it obtains tokens *from* other MCP servers. Its
   identity is a **Client ID Metadata Document** at `/.well-known/oauth-client`.
   Under CIMD the `client_id` is literally that URL, so any MCP authorization
   server can identify this app by fetching it — no pre-registration, no shared
   secret. Where a server does not support CIMD, the app falls back to dynamic
   client registration (RFC 7591), then to a statically configured client ID
   (needed for Google).

### Connection states

`status` and `is_enabled` are deliberately independent:

| Action | Tokens | Tools visible to the LLM | Re-auth needed |
|---|---|---|---|
| **Disable** | kept, encrypted | no | no |
| **Enable** | kept | yes | no |
| **Disconnect** | deleted | no | yes |
| **Delete** | deleted | no | yes |

That is what makes "turn a connector off without signing out again" work.

### Token security

Third-party OAuth tokens never touch a plain column. They are encrypted with
`pgp_sym_encrypt(..., 'cipher-algo=aes256')` into `bytea`, using a key held in
application config and passed as a **bound parameter**, so it never reaches
`pg_stat_statements` or the query log. A stolen database dump is not enough to
decrypt them.

Our own session tokens are handled separately: refresh tokens are stored only as
SHA-256 hashes, are rotated on every use, and reuse of a rotated token revokes
the whole family.

---

## Layout

```
apps/
  host-backend/
    app/
      api/routes/   auth, oauth_server, connectors, chat, well_known
      core/         config, security (JWT/PKCE/passwords)
      db/           session, base, seed
      mcp/          discovery, client_manager, registry, token_store
      models/       user, connector, chat
      schemas/      request/response models
      services/     auth, oauth_server, connector, chat, llm, crypto
    alembic/        migrations
    tests/
  web/
    src/app/        login, chat, settings/connectors
    src/components/ app-shell, connector-card, tool-call-card, ui/
    src/hooks/      use-auth, use-connectors, use-chat
    src/lib/        api client, types
infra/              docker-compose, init-db.sql
docs/
  PLAN.md           the six-day build sequence
  AUTH.md           sessions, our OAuth server, and us as an OAuth client
  DATABASE.md       data layer, storage, migrations, running SQL
```

---

## Schema changes

> Full details — layers, session lifecycle, the FK graph, Docker storage,
> running SQL, and migrating to a managed database — are in
> **[docs/DATABASE.md](docs/DATABASE.md)**.

Alembic owns the schema in every environment, including local. The app never
calls `create_all` — two mechanisms building the same tables is how a database
silently drifts from the models, and it always surfaces in production.

After editing anything in `app/models/`:

```bash
cd apps/host-backend
uv run alembic revision --autogenerate -m "what changed"
uv run alembic check          # confirms models and migrations agree
uv run alembic upgrade head
```

The app verifies the applied revision on start-up and refuses to serve against
a stale database, naming the command that fixes it. Always read a generated
migration before applying it: autogenerate misses table/column renames (it sees
a drop plus an add) and most server-default changes.

Useful commands: `alembic current` (where the DB is), `alembic history`,
`alembic downgrade -1` (undo one).

## Tests

```bash
cd apps/host-backend && uv run pytest -q     # 32 tests
cd apps/web && npx tsc --noEmit && npm run build
```

No configuration is required. Unit tests build their own `Settings` and never
read a `.env`, so they never depend on your API key or database.

Four tests in `tests/test_auth_flow.py` drive a **live** backend, because they
cover behaviour that only appears through the real dependency stack — refresh
rotation, and the fact that reuse detection must *commit* its revocation even
though the request fails. They skip automatically when no backend is reachable,
so a fresh checkout still gets a green run. Point them elsewhere with
`TEST_BACKEND_URL` (see `tests/.env.example`).

---

## Roadmap

| Day | Deliverable |
|-----|-------------|
| 1 ✅ | Host backend, auth server + CIMD, database, chat UI, connectors UI |
| 2 | MCP-1 (own auth server) and MCP-3 (no auth) |
| 3 | MCP-2 (forward auth) and MCP-5 (Keycloak/Scalekit, CIMD) |
| 4 | MCP-4 and Google Workspace (real Google OAuth) |
| 5 | Deployment: Vercel + Fly/Render + Neon, all on free tiers |
| 6 | Hardening: rate limits, audit log, RS256 JWKS, E2E tests |
```
