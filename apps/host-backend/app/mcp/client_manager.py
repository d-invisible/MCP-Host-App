"""Per-connection MCP clients.

Each connected+enabled MCP server gets its own client. Sessions are opened per
operation rather than held open indefinitely: Streamable HTTP in the 2026 spec
is stateless, so a fresh session costs one round-trip and avoids the failure
modes of long-lived sockets (idle timeouts, silent half-open connections, and
tokens that expire mid-session).

Access tokens are refreshed here, before the request, because the Agents SDK's
own MCP client takes static headers and cannot refresh mid-run.
"""

from __future__ import annotations

import logging
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from sqlalchemy.ext.asyncio import AsyncSession

from app.mcp.token_store import EncryptedTokenStorage
from app.models import AuthKind, ConnectionStatus, MCPConnection
from app.services import crypto_service

logger = logging.getLogger(__name__)

# Refresh a token slightly before it actually expires, so a long tool call
# cannot straddle the expiry boundary.
_REFRESH_SKEW = timedelta(seconds=60)


class MCPConnectionError(Exception):
    """Raised when a connection cannot be established or used."""


def _root_cause(exc: BaseException) -> str:
    """Flatten an ExceptionGroup down to a message a user can act on.

    anyio task groups wrap transport failures in an ExceptionGroup, whose own
    message ("unhandled errors in a TaskGroup") says nothing useful.
    """
    inner = getattr(exc, "exceptions", None)
    if inner:
        return "; ".join(_root_cause(sub) for sub in inner)
    message = str(exc).strip()
    return message or exc.__class__.__name__


@dataclass(slots=True)
class ToolDescriptor:
    """A tool exposed by a connected MCP server, in provider-neutral form."""

    name: str
    description: str
    input_schema: dict[str, Any]
    connection_id: uuid.UUID
    connector_slug: str
    connector_name: str

    @property
    def qualified_name(self) -> str:
        """Namespaced name given to the LLM.

        Two servers may each expose a `search` tool; prefixing with the
        connector slug keeps them distinct and lets us route a call back to the
        right connection.
        """
        return f"{self.connector_slug}__{self.name}"


class MCPClientManager:
    """Opens authenticated MCP sessions and performs tool operations."""

    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    # -- session -----------------------------------------------------------
    @asynccontextmanager
    async def session(self, connection: MCPConnection):
        """Yield an initialized `Client` for a connection."""
        headers: dict[str, str] = {}

        if connection.connector.auth_kind is not AuthKind.NONE:
            token = await self._get_valid_access_token(connection)
            if token is None:
                raise MCPConnectionError(
                    f"No valid credentials for {connection.connector.name}. Please reconnect."
                )
            headers["Authorization"] = f"Bearer {token}"

        url = connection.connector.server_url
        try:
            async with httpx.AsyncClient(
                headers=headers, timeout=30.0, follow_redirects=True
            ) as http_client:
                # `Client` enters the transport itself, so the context manager
                # is handed over un-entered.
                transport = streamable_http_client(url, http_client=http_client)
                async with Client(transport) as client:
                    yield client
        except MCPConnectionError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise MCPConnectionError(
                f"Could not reach {connection.connector.name}: {_root_cause(exc)}"
            ) from exc

    # -- operations --------------------------------------------------------
    async def list_tools(self, connection: MCPConnection) -> list[ToolDescriptor]:
        async with self.session(connection) as client:
            result = await client.list_tools()

        return [
            ToolDescriptor(
                name=tool.name,
                description=tool.description or "",
                input_schema=tool.input_schema or {"type": "object", "properties": {}},
                connection_id=connection.id,
                connector_slug=connection.connector.slug,
                connector_name=connection.connector.name,
            )
            for tool in result.tools
        ]

    async def call_tool(
        self,
        connection: MCPConnection,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> tuple[str, bool]:
        """Invoke a tool. Returns (text_content, is_error)."""
        async with self.session(connection) as client:
            result = await client.call_tool(tool_name, arguments)

        parts: list[str] = []
        for block in result.content:
            text = getattr(block, "text", None)
            parts.append(text if text is not None else str(block))

        return "\n".join(parts).strip(), bool(getattr(result, "is_error", False))

    async def sync_tools(self, connection: MCPConnection) -> list[ToolDescriptor]:
        """Refresh the cached tool catalogue, recording success or failure."""
        try:
            tools = await self.list_tools(connection)
        except MCPConnectionError as exc:
            connection.status = ConnectionStatus.ERROR
            connection.last_error = str(exc)
            await self._db.flush()
            raise

        connection.tools_cache = [
            {
                "name": t.name,
                "description": t.description,
                "input_schema": t.input_schema,
            }
            for t in tools
        ]
        connection.tools_synced_at = datetime.now(UTC)
        connection.status = ConnectionStatus.CONNECTED
        connection.last_connected_at = datetime.now(UTC)
        connection.last_error = None
        await self._db.flush()
        return tools

    # -- tokens ------------------------------------------------------------
    async def _get_valid_access_token(self, connection: MCPConnection) -> str | None:
        """Return a live access token, refreshing it first when near expiry."""
        storage = EncryptedTokenStorage(self._db, connection.id)
        tokens = await storage.get_tokens()
        if tokens is None:
            return None

        credential = await storage._load()  # noqa: SLF001 - same module family
        needs_refresh = (
            credential is not None
            and credential.expires_at is not None
            and credential.expires_at - _REFRESH_SKEW <= datetime.now(UTC)
        )
        if not needs_refresh:
            return tokens.access_token

        if not tokens.refresh_token:
            connection.status = ConnectionStatus.EXPIRED
            connection.last_error = "Access token expired and no refresh token is available"
            await self._db.flush()
            return None

        refreshed = await self._refresh_token(connection, tokens.refresh_token)
        if refreshed is None:
            return None
        return refreshed

    async def _refresh_token(self, connection: MCPConnection, refresh_token: str) -> str | None:
        from app.mcp.discovery import DiscoveryError, discover

        try:
            discovered = await discover(connection.connector.server_url)
        except DiscoveryError as exc:
            logger.warning("Token refresh discovery failed for %s: %s", connection.id, exc)
            return None

        from app.core.config import settings

        data = {
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": connection.remote_client_id or settings.cimd_url,
        }
        if discovered.resource:
            data["resource"] = discovered.resource

        storage = EncryptedTokenStorage(self._db, connection.id)
        credential = await storage._load()  # noqa: SLF001
        if credential is not None and credential.client_secret_enc is not None:
            secret = await crypto_service.decrypt(self._db, credential.client_secret_enc)
            if secret:
                data["client_secret"] = secret

        try:
            async with httpx.AsyncClient(timeout=15.0) as http:
                resp = await http.post(discovered.token_endpoint, data=data)
        except httpx.HTTPError as exc:
            logger.warning("Token refresh request failed for %s: %s", connection.id, exc)
            return None

        if resp.status_code != 200:
            if credential is not None:
                credential.refresh_failures += 1
            connection.status = ConnectionStatus.EXPIRED
            connection.last_error = (
                f"Token refresh rejected by the authorization server ({resp.status_code}). "
                "Please reconnect."
            )
            await self._db.flush()
            return None

        from mcp.shared.auth import OAuthToken

        payload = resp.json()
        await storage.set_tokens(
            OAuthToken(
                access_token=payload["access_token"],
                token_type=payload.get("token_type", "Bearer"),
                expires_in=payload.get("expires_in"),
                refresh_token=payload.get("refresh_token") or refresh_token,
                scope=payload.get("scope"),
            )
        )
        connection.status = ConnectionStatus.CONNECTED
        connection.last_error = None
        await self._db.flush()
        return payload["access_token"]
