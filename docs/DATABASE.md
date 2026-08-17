# Database guide

How data flows from an HTTP request down to Postgres, how the local Docker
setup stores it, and what changes when you move to a managed database.

---

## 1. The layers

```
  HTTP request
       │
       ▼
┌──────────────────────────────────────────────────────────┐
│ ROUTE          app/api/routes/*.py                       │
│                HTTP only: status codes, request bodies.  │
│                Declares `db: DbSession` and gets one.    │
└──────────────────────────┬───────────────────────────────┘
                           │
       ┌───────────────────▼───────────────────┐
       │ DEPENDENCY   app/api/deps.py          │
       │              get_db() opens a session │
       │              and commits or rolls back│
       └───────────────────┬───────────────────┘
                           │
┌──────────────────────────▼───────────────────────────────┐
│ SERVICE        app/services/*.py                         │
│                Business logic and queries.               │
│                Raises domain errors, never HTTPException.│
└──────────────────────────┬───────────────────────────────┘
                           │
┌──────────────────────────▼───────────────────────────────┐
│ MODEL          app/models/*.py                           │
│                Python class ⟷ table definition.          │
└──────────────────────────┬───────────────────────────────┘
                           │  SQLAlchemy → asyncpg
┌──────────────────────────▼───────────────────────────────┐
│ POSTGRES 17    in Docker, port 5432                      │
└──────────────────────────────────────────────────────────┘
```

The rule that keeps this clean:

> **Services never import FastAPI. Routes never write SQL.**

That is why `connector_service` raises `ConnectorError` and the route turns it
into a 404 — the service stays usable from a CLI script or a background job,
not just from HTTP.

---

## 2. Model → table

A model *is* the table definition. From `app/models/connector.py`:

```python
class MCPConnection(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "mcp_connections"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE")
    )
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True)

    credential: Mapped[MCPCredential | None] = relationship(
        cascade="all, delete-orphan"
    )
```

* `Mapped[bool]` declares the Python type *and* the SQL column together.
* `relationship()` is **not** a column. It is a navigation link, so
  `connection.credential` either triggers a query or uses pre-loaded data.

### How `db.add()` knows the table

Inheriting from `Base` triggers SQLAlchemy's declarative machinery, which
records `class → table` in a registry **at import time**. So this needs no
table name:

```python
conversation = Conversation(user_id=user.id, title="New chat")
db.add(conversation)
```

```
db.add(conversation)
   └─ type(conversation)        → Conversation
      └─ registry lookup        → mapper
         └─ mapper.local_table  → "conversations"
```

Pass a `Message` and it resolves to `messages`. Pass an unmapped object and the
session raises `UnmappedInstanceError`.

### Object states

`add()` writes no SQL. It moves the object into the session's *pending* set:

| State | When | Row exists? |
|---|---|---|
| `transient` | just constructed | no |
| `pending` | after `db.add()` | no — queued only |
| `persistent` | after `db.flush()` | yes, inside the transaction |

This is why `create_conversation` flushes before returning: before the flush,
`conversation.id` is still `None`, and the route needs it.

### When `add()` is not needed

```python
connection.is_enabled = enabled   # object was loaded, so already tracked
await db.flush()                  # SQLAlchemy emits the UPDATE
```

An object loaded from the database is already in the session. SQLAlchemy
compares attributes against their loaded values and writes an `UPDATE` for
whatever changed — the *unit of work* pattern. You never hand-write `UPDATE`.

> **Consequence:** mutating a loaded object persists at commit whether you
> intended it or not. There is no "save" call to forget.

---

## 3. Session and transactions

`app/db/session.py` builds the engine once at import — a pool of 10 connections
reused across requests, because a new Postgres connection costs milliseconds.

```python
async with SessionLocal() as session:
    try:
        yield session
        await session.commit()      # request succeeded
    except Exception:
        await session.rollback()    # anything raised → nothing persists
        raise
```

**One request = one transaction.**

```
request ──┬─ service call ─ flush ──┐
          ├─ service call ─ flush ──┤ all in ONE transaction
          └─ service call ─ flush ──┘
                                     │
              success ───► COMMIT ───┤
              exception ─► ROLLBACK ─┘
```

So services call `await db.flush()` (send SQL, get generated IDs, stay in the
transaction) and almost never `commit()` — the dependency owns that decision.
If `connect` succeeds but the tool sync then fails, both roll back together.

`expire_on_commit=False` matters: without it, reading `user.email` after a
commit would fire a fresh lazy query and blow up in async code.

### The one deliberate exception

`app/api/routes/chat.py` opens its **own** session for streaming:

```python
async def event_stream():
    async with SessionLocal() as session:   # not the request session
```

The endpoint *returns* a `StreamingResponse` immediately, so `get_db()` has
already closed the request session by the time the generator runs. Reusing it
would fail on the first query mid-stream.

---

## 4. The schema

```
                        ┌─────────────┐
                        │    users    │
                        └──────┬──────┘
          ┌────────────────────┼────────────────────┐
          │ CASCADE            │ CASCADE            │ CASCADE
          ▼                    ▼                    ▼
   ┌──────────────┐   ┌─────────────────┐   ┌───────────────┐
   │refresh_tokens│   │  conversations  │   │mcp_connections│
   └──────────────┘   └────────┬────────┘   └───────┬───────┘
                               │ CASCADE            │
                               ▼                    │ CASCADE
                        ┌─────────────┐             ▼
                        │  messages   │     ┌───────────────┐
                        └──────┬──────┘     │mcp_credentials│
                               │ CASCADE    │  (encrypted)  │
                               ▼            └───────────────┘
                        ┌─────────────┐             ▲
                        │ tool_calls  │─────────────┘
                        └─────────────┘   SET NULL
                                          (keeps history)

   mcp_connectors ──CASCADE──► mcp_connections   (the catalogue)
```

Delete rules are enforced by Postgres, so they hold even when data is edited
outside the app.

| Child | Parent | On delete | Why |
|---|---|---|---|
| `mcp_credentials` | `mcp_connections` | CASCADE | tokens must never outlive their connection |
| `mcp_connections` | `users`, `mcp_connectors` | CASCADE | no orphan connections |
| `messages` | `conversations` | CASCADE | messages have no meaning alone |
| **`tool_calls`** | **`mcp_connections`** | **SET NULL** | **chat history survives deleting a connector** |

That `SET NULL` is the one deliberate exception. Cascading there would silently
delete parts of a user's conversation history when they removed a connector.

### Connection state — two independent flags

`status` and `is_enabled` are separate on purpose:

| Action | `status` | `is_enabled` | Tokens | Tools visible to LLM | Re-auth? |
|---|---|---|---|---|---|
| Disable | `connected` | `false` | kept | no | no |
| Enable | `connected` | `true` | kept | yes | no |
| Disconnect | `disconnected` | — | **deleted** | no | yes |
| Delete | row removed | — | **deleted** | no | yes |

This is what makes "turn a connector off without signing in again" work.

---

## 5. Token encryption

Third-party OAuth tokens never touch a readable column.

```
 plaintext token
       │
       ▼   pgp_sym_encrypt(value, key, 'cipher-algo=aes256')
 ┌───────────────────────────────┐
 │ mcp_credentials               │      key lives in .env / platform secrets
 │   access_token_enc   bytea    │ ◄─── never stored in the database
 │   refresh_token_enc  bytea    │
 │   client_secret_enc  bytea    │
 └───────────────────────────────┘
```

The key is always passed as a **bound parameter**, so it never appears in
`pg_stat_statements` or the query log. A stolen database dump cannot be
decrypted.

In a DB viewer these columns look like `\x c30d0409...` — that is correct. To
read one deliberately:

```sql
SELECT pgp_sym_decrypt(access_token_enc, 'YOUR_TOKEN_ENCRYPTION_KEY')
FROM mcp_credentials;
```

> **Never rotate `TOKEN_ENCRYPTION_KEY` once tokens exist.** Every stored token
> becomes undecryptable and every user must reconnect.

Session tokens are handled separately: refresh tokens are stored only as
SHA-256 hashes, rotated on every use, and reuse of a rotated token revokes the
whole family.

---

## 6. Local Docker setup

```
  Windows host                     Docker VM
 ┌───────────────┐                ┌──────────────────────────────┐
 │ backend       │                │  container                   │
 │  :8000        │──127.0.0.1:5432│  mcp-host-postgres           │
 │ DB client     │───────────────►│  postgres:17-alpine          │
 └───────────────┘                │        │                     │
                                  │        ▼ /var/lib/.../data   │
                                  │  ┌────────────────────────┐  │
                                  │  │ volume mcp-host_pgdata │  │
                                  │  │        ~47 MB          │  │
                                  │  └────────────────────────┘  │
                                  └──────────────────────────────┘
```

**Data does not live in your project folder.** It is in a Docker-managed
volume:

```
mcp-host_pgdata → /var/lib/docker/volumes/mcp-host_pgdata/_data
```

On Windows that path is inside the Rancher/WSL VM, not on your `D:` drive. The
volume is independent of the container:

| Command | Data |
|---|---|
| `docker compose restart` / `stop` | kept |
| `docker compose down` | kept — the volume is not removed |
| **`docker compose down -v`** | **destroyed** |

`npm run db:reset` uses `-v`. It is the one command that deletes everything.

### The init-script gotcha

`infra/init-db.sql` is mounted at `/docker-entrypoint-initdb.d/`, and those
scripts run **only when the data directory is empty**. Editing that file does
nothing to an existing volume.

Proof from this very project: `uuid-ossp` is listed in `init-db.sql` but is
*not* installed, because the volume predates the file. Nothing breaks (UUIDs
are generated in Python, and `pgcrypto` is created by the migration), but it is
a trap worth knowing.

### Connecting a DB viewer

| Field | Value |
|---|---|
| Host | `127.0.0.1` |
| Port | `5432` |
| Database | `mcphost` |
| User | `mcphost` |
| Password | `mcphost` |
| SSL | **disable** |

Prefer `127.0.0.1` over `localhost`: on Windows `localhost` may resolve to IPv6
`::1` first, which some clients mishandle.

As a URL — note there is **no** `+asyncpg`, that prefix is SQLAlchemy-only and
GUI tools do not understand it:

```
postgresql://mcphost:mcphost@127.0.0.1:5432/mcphost?sslmode=disable
```

Common errors:

| Error | Cause |
|---|---|
| `database "mcphost " does not exist` | trailing space — the name is exactly 7 characters |
| `Connection lost before handshake` | client set to MySQL, or SSL enabled |
| connection refused | container not running: `docker compose up -d` |

---

## 7. Migrations

**Alembic is the only thing that creates or alters tables, in every
environment.** The app never calls `create_all`.

Two mechanisms building the same schema is how a database drifts from the
models: a new model gets created locally by `create_all`, no migration is ever
written, and the failure appears only when production deploys.

On start-up the app checks the applied revision and refuses to serve a stale
database:

```
The database is at migration '0000deadbeef' but the code expects '3004f9b8c24b'.
Bring it up to date with:
    uv run alembic upgrade head
```

### Workflow after changing anything in `app/models/`

```bash
cd apps/host-backend
uv run alembic revision --autogenerate -m "what changed"
uv run alembic check            # confirms models and migrations agree
uv run alembic upgrade head
```

`alembic check` is the guard against forgetting a migration. It belongs in CI.

> **Always read a generated migration before applying it.** Autogenerate
> handles added and dropped tables and columns, but it **cannot see a rename** —
> renaming `title` to `name` produces a `DROP` plus an `ADD`, which destroys
> that column's data. Hand-edit those to
> `op.alter_column(..., new_column_name=...)`. Most server-default changes are
> missed too.

Other commands: `alembic current`, `alembic history`, `alembic downgrade -1`.

---

## 8. Running SQL

Four options, roughly in order of daily usefulness.

**1. Your DB client** — best for browsing and iterating.

**2. The helper script** — uses the app's own settings, so it follows
`DATABASE_URL` when you move to a managed database:

```bash
cd apps/host-backend
uv run python scripts/sql.py "SELECT slug, auth_kind FROM mcp_connectors"
uv run python scripts/sql.py -f query.sql
uv run python scripts/sql.py                      # interactive
uv run python scripts/sql.py "SELECT * FROM users WHERE email = :e" -p e=a@b.com
```

It commits automatically for writes, and renders `bytea` as
`<N bytes, encrypted>` instead of dumping binary.

**3. psql in the container:**

```bash
docker exec -it mcp-host-postgres psql -U mcphost -d mcphost
```

Useful: `\dt` (tables), `\d messages` (one table), `\q`.

**4. In application code** — only when the ORM cannot express it.
`crypto_service.py` is the legitimate case, since `pgp_sym_encrypt` has no ORM
equivalent:

```python
from sqlalchemy import text

result = await db.execute(
    text("SELECT * FROM users WHERE email = :email"),
    {"email": email},          # bound parameter
)
```

> **Always use `:name` placeholders with a params dict.** Never build SQL with
> f-strings or `+`. Beyond preventing injection, bound parameters keep values
> out of the query log — which is exactly why the encryption key is passed this
> way.

Most queries need no raw SQL at all:

```python
users = await db.scalars(select(User).where(User.email == email))
```

Set `DB_ECHO=true` in `.env` to log every statement SQLAlchemy emits — useful
for learning and for spotting N+1 queries.

### A query across the whole chain

```sql
SELECT u.email, c.title, m.role, left(m.content, 60) AS preview,
       tc.tool_name, tc.status
FROM users u
JOIN conversations c    ON c.user_id = u.id
JOIN messages m         ON m.conversation_id = c.id
LEFT JOIN tool_calls tc ON tc.message_id = m.id
ORDER BY m.created_at DESC;
```

> While browsing, remember the app assumes it owns this data. Editing
> `mcp_connections.status` or anything in `mcp_credentials` by hand can desync
> application state. Read freely; write through the API.

---

## 9. Moving to a real database

Genuinely little changes — **only the connection string**:

```bash
DATABASE_URL=postgresql+asyncpg://user:pass@ep-xyz.neon.tech/mcphost?ssl=require
```

No model, service, or route changes. That is the payoff of the layering.

What does change:

**Schema creation.** Run `alembic upgrade head` as a release step, before the
new version serves traffic. The start-up guard enforces this.

**Verify `pgcrypto` is available.** Neon and Supabase support it; some managed
providers restrict extensions, and `CREATE EXTENSION` needs sufficient
privileges. **Check this before committing to a provider** — token encryption
depends on it.

**Pooling.** `pool_size=10, max_overflow=20` allows up to 30 connections *per
instance*. Free tiers cap well below that, and serverless platforms multiply it
per instance. Use the provider's pooled connection string, or lower the numbers.

**TLS and secrets.** Add `?ssl=require`; move both keys to platform secrets and
generate fresh ones — but never rotate `TOKEN_ENCRYPTION_KEY` after tokens
exist.

**Backups.** The Docker volume is currently your only copy. Managed providers
give point-in-time restore, which is a real reason to move.

To keep a copy of local data first:

```bash
docker exec mcp-host-postgres pg_dump -U mcphost mcphost > backup.sql
```

---

## Table reference

| Table | Purpose |
|---|---|
| `users` | accounts for this app |
| `refresh_tokens` | session refresh tokens, stored as SHA-256 hashes |
| `oauth_clients` | clients registered against *our* OAuth server |
| `authorization_codes` | short-lived codes issued by our OAuth server |
| `mcp_connectors` | catalogue of MCP servers (6 seeded) |
| `mcp_connections` | one user's connection to one connector |
| `mcp_credentials` | **encrypted** OAuth tokens for a connection |
| `oauth_transactions` | in-flight outbound OAuth state + PKCE verifier |
| `conversations` | chat threads |
| `messages` | user and assistant turns |
| `tool_calls` | MCP tool invocations, with arguments and results |
| `alembic_version` | current migration revision |
