"""FastAPI application entrypoint."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy.engine import Connection

from app.api.routes import auth, chat, connectors, oauth_server, well_known
from app.core.config import settings
from app.db.seed import seed_connectors
from app.db.session import SessionLocal, engine

logging.basicConfig(
    level=logging.DEBUG if settings.debug else logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Verify the schema and seed reference data on start-up.

    Alembic is the *only* thing that creates or alters tables, in every
    environment. The app never calls `create_all`: having two mechanisms build
    the schema lets them drift, and the failure shows up in production, where a
    model change worked locally but no migration was ever written for it.

    So this only checks, and refuses to serve against a stale database.
    """
    async with engine.begin() as conn:
        await conn.exec_driver_sql("CREATE EXTENSION IF NOT EXISTS pgcrypto")
        await conn.run_sync(_verify_schema_is_current)

    async with SessionLocal() as session:
        count = await seed_connectors(session)
        await session.commit()
        if count:
            logger.info("Seeded %d built-in connectors", count)

    logger.info("%s ready at %s", settings.app_name, settings.backend_base_url)
    yield
    await engine.dispose()


def _verify_schema_is_current(connection: Connection) -> None:
    """Fail fast when the database is not migrated up to head.

    Runs synchronously inside `run_sync` because Alembic's migration context
    works on a DBAPI connection, not an async one.
    """
    script = ScriptDirectory.from_config(
        Config(str(Path(__file__).resolve().parent.parent / "alembic.ini"))
    )
    head = script.get_current_head()
    current = MigrationContext.configure(connection).get_current_revision()

    if current == head:
        return

    if current is None:
        raise RuntimeError(
            "The database has no schema yet. Create it with:\n"
            "    uv run alembic upgrade head"
        )
    raise RuntimeError(
        f"The database is at migration {current!r} but the code expects {head!r}.\n"
        "Bring it up to date with:\n"
        "    uv run alembic upgrade head"
    )


app = FastAPI(
    title="MCP Host API",
    description="Chat host with pluggable MCP connectors and OAuth.",
    version="0.1.0",
    lifespan=lifespan,
    docs_url="/docs",
    openapi_url="/openapi.json",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Discovery documents must sit at the root, not under /api.
app.include_router(well_known.router)
app.include_router(oauth_server.router)
app.include_router(auth.router)
app.include_router(connectors.router)
app.include_router(chat.router)


@app.get("/health", tags=["ops"])
async def health() -> JSONResponse:
    """Liveness probe that also verifies the database is reachable."""
    from sqlalchemy import text

    db_ok = True
    try:
        async with SessionLocal() as session:
            await session.execute(text("SELECT 1"))
    except Exception:  # noqa: BLE001
        db_ok = False
        logger.exception("Health check could not reach the database")

    return JSONResponse(
        {
            "status": "ok" if db_ok else "degraded",
            "database": "up" if db_ok else "down",
            "environment": settings.environment,
            "llm_configured": bool(settings.llm_api_key),
            "llm_provider": "azure-ai-foundry" if settings.is_azure_foundry else "openai",
            "llm_model": settings.llm_model,
        },
        status_code=200 if db_ok else 503,
    )
