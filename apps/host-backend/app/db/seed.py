"""Seed the connector catalogue with the six demo MCP servers.

The servers themselves are built on later days; these rows describe where they
will live and how each one authenticates, so the UI is populated from Day 1.
Ports are assigned here and must match the servers when they are built.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AuthKind, MCPConnector

BUILTIN_CONNECTORS: list[dict] = [
    {
        "slug": "notes",
        "name": "Notes",
        "description": "Personal notes with its own dedicated authorization server.",
        "server_url": "http://localhost:8101/mcp",
        "auth_kind": AuthKind.OAUTH,
        "scopes": ["notes:read", "notes:write"],
        "icon": "notebook-pen",
    },
    {
        "slug": "tasks",
        "name": "Tasks",
        "description": "Task tracker that reuses this app's own sign-in (forwarded auth).",
        "server_url": "http://localhost:8102/mcp",
        "auth_kind": AuthKind.FORWARD,
        "scopes": ["chat", "profile"],
        "icon": "check-square",
    },
    {
        "slug": "public-tools",
        "name": "Public Tools",
        "description": "Open utilities (time, unit conversion, hashing). No sign-in required.",
        "server_url": "http://localhost:8103/mcp",
        "auth_kind": AuthKind.NONE,
        "scopes": [],
        "icon": "wrench",
    },
    {
        "slug": "gdrive-lite",
        "name": "Drive Lite",
        "description": "Google-authenticated demo server. Needs a Google Cloud client ID.",
        "server_url": "http://localhost:8104/mcp",
        "auth_kind": AuthKind.OAUTH,
        "scopes": ["openid", "email", "profile"],
        "icon": "hard-drive",
    },
    {
        "slug": "crm",
        "name": "CRM",
        "description": "Uses a decentralized identity provider (Keycloak/Scalekit) with CIMD.",
        "server_url": "http://localhost:8105/mcp",
        "auth_kind": AuthKind.OAUTH,
        "scopes": ["crm:read"],
        "icon": "users",
    },
    {
        "slug": "google-workspace",
        "name": "Google Workspace",
        "description": "Real Google APIs (Calendar/Gmail). Requires cloud app registration.",
        "server_url": "http://localhost:8106/mcp",
        "auth_kind": AuthKind.OAUTH,
        "scopes": [
            "openid",
            "email",
            "https://www.googleapis.com/auth/calendar.readonly",
        ],
        "icon": "calendar",
    },
]


async def seed_connectors(db: AsyncSession) -> int:
    """Insert any missing built-in connectors. Idempotent.

    Existing rows are left alone so a user's edits to a connector survive
    restarts.
    """
    inserted = 0
    for entry in BUILTIN_CONNECTORS:
        existing = await db.scalar(
            select(MCPConnector).where(MCPConnector.slug == entry["slug"])
        )
        if existing is not None:
            continue

        db.add(MCPConnector(**entry, is_builtin=True, is_enabled=True))
        inserted += 1

    if inserted:
        await db.flush()
    return inserted
