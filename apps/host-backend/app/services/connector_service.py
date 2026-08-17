"""Connector lifecycle: connect, authorize, enable/disable, disconnect, delete."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import httpx
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.security import (
    code_challenge_from_verifier,
    generate_code_verifier,
    random_urlsafe,
)
from app.mcp.discovery import DiscoveredAuth, DiscoveryError, discover
from app.mcp.token_store import EncryptedTokenStorage
from app.models import (
    AuthKind,
    ConnectionStatus,
    MCPConnection,
    MCPConnector,
    MCPCredential,
    OAuthTransaction,
    User,
)
from app.services import crypto_service

OAUTH_TRANSACTION_TTL = timedelta(minutes=10)


class ConnectorError(Exception):
    """Raised for user-correctable connector problems."""


async def list_connectors_for_user(
    db: AsyncSession, user: User
) -> list[tuple[MCPConnector, MCPConnection | None]]:
    """Every catalogue entry, paired with this user's connection if any."""
    connectors = list(
        await db.scalars(select(MCPConnector).order_by(MCPConnector.name))
    )
    connections = {
        conn.connector_id: conn
        for conn in await db.scalars(
            select(MCPConnection).where(MCPConnection.user_id == user.id)
        )
    }
    return [(connector, connections.get(connector.id)) for connector in connectors]


async def get_connection(
    db: AsyncSession, user: User, connection_id: uuid.UUID
) -> MCPConnection:
    connection = await db.get(MCPConnection, connection_id)
    if connection is None or connection.user_id != user.id:
        raise ConnectorError("Connection not found")
    return connection


async def begin_connect(
    db: AsyncSession,
    user: User,
    connector_id: uuid.UUID,
) -> tuple[MCPConnection, str | None]:
    """Start a connection.

    Returns the connection plus an authorization URL when the user must be
    redirected. For a no-auth server the URL is None and the connection is
    usable immediately.
    """
    connector = await db.get(MCPConnector, connector_id)
    if connector is None:
        raise ConnectorError("Connector not found")
    if not connector.is_enabled:
        raise ConnectorError(f"{connector.name} is currently disabled")

    connection = await db.scalar(
        select(MCPConnection).where(
            MCPConnection.user_id == user.id,
            MCPConnection.connector_id == connector.id,
        )
    )
    if connection is None:
        connection = MCPConnection(
            user_id=user.id,
            connector_id=connector.id,
            status=ConnectionStatus.PENDING,
            is_enabled=True,
        )
        db.add(connection)
        await db.flush()
        await db.refresh(connection, ["connector"])

    if connector.auth_kind is AuthKind.NONE:
        connection.status = ConnectionStatus.CONNECTED
        connection.last_connected_at = datetime.now(UTC)
        connection.last_error = None
        await db.flush()
        return connection, None

    authorization_url = await _start_oauth(db, user, connection, connector)
    return connection, authorization_url


async def _start_oauth(
    db: AsyncSession,
    user: User,
    connection: MCPConnection,
    connector: MCPConnector,
) -> str:
    """Discover the AS, register if needed, and build the authorization URL."""
    try:
        discovered = await discover(connector.server_url)
    except DiscoveryError as exc:
        connection.status = ConnectionStatus.ERROR
        connection.last_error = str(exc)
        await db.flush()
        raise ConnectorError(
            f"{connector.name} did not advertise an OAuth authorization server. {exc}"
        ) from exc

    client_id, client_secret = await _obtain_client_credentials(
        db, connector, connection, discovered
    )

    verifier = generate_code_verifier()
    state = random_urlsafe(32)

    scopes = connector.scopes or discovered.scopes_supported or []

    db.add(
        OAuthTransaction(
            state=state,
            user_id=user.id,
            connection_id=connection.id,
            code_verifier=verifier,
            authorization_endpoint=discovered.authorization_endpoint,
            token_endpoint=discovered.token_endpoint,
            registration_endpoint=discovered.registration_endpoint,
            resource=discovered.resource,
            client_id=client_id,
            client_secret_enc=await crypto_service.encrypt(db, client_secret),
            scopes=scopes,
            expires_at=datetime.now(UTC) + OAUTH_TRANSACTION_TTL,
        )
    )

    connection.status = ConnectionStatus.PENDING
    connection.remote_client_id = client_id
    await db.flush()

    from urllib.parse import urlencode

    params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": settings.mcp_oauth_redirect_uri,
        "state": state,
        "code_challenge": code_challenge_from_verifier(verifier),
        "code_challenge_method": "S256",
    }
    if scopes:
        params["scope"] = " ".join(scopes)
    # RFC 8707: bind the token to this specific MCP server.
    if discovered.resource:
        params["resource"] = discovered.resource

    separator = "&" if "?" in discovered.authorization_endpoint else "?"
    return f"{discovered.authorization_endpoint}{separator}{urlencode(params)}"


async def _obtain_client_credentials(
    db: AsyncSession,
    connector: MCPConnector,
    connection: MCPConnection,
    discovered: DiscoveredAuth,
) -> tuple[str, str | None]:
    """Decide how this host app identifies itself to the remote AS.

    Preference order:
      1. a statically configured client id (Google and similar, which require
         a pre-registered cloud application)
      2. CIMD — our client_id is the URL of our metadata document
      3. dynamic client registration (RFC 7591)
      4. the CIMD URL as a last resort
    """
    if connector.static_client_id:
        return connector.static_client_id, None

    if connection.remote_client_id:
        storage = EncryptedTokenStorage(db, connection.id)
        info = await storage.get_client_info()
        if info is not None:
            return info.client_id, info.client_secret

    if discovered.supports_cimd:
        return settings.cimd_url, None

    if discovered.registration_endpoint:
        registration = {
            "client_name": settings.app_name,
            "redirect_uris": [settings.mcp_oauth_redirect_uri],
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"],
            "token_endpoint_auth_method": "none",
            "client_uri": settings.frontend_base_url,
        }
        if connector.scopes:
            registration["scope"] = " ".join(connector.scopes)

        try:
            async with httpx.AsyncClient(timeout=15.0) as http:
                resp = await http.post(discovered.registration_endpoint, json=registration)
            if resp.status_code in (200, 201):
                payload = resp.json()
                return payload["client_id"], payload.get("client_secret")
        except (httpx.HTTPError, KeyError, ValueError):
            pass  # fall through to CIMD

    return settings.cimd_url, None


async def complete_oauth(
    db: AsyncSession,
    *,
    state: str,
    code: str,
) -> MCPConnection:
    """Handle the OAuth callback: exchange the code and store the tokens."""
    transaction = await db.scalar(
        select(OAuthTransaction).where(OAuthTransaction.state == state)
    )
    if transaction is None:
        raise ConnectorError("This authorization request is unknown or has already been used")
    if transaction.expires_at <= datetime.now(UTC):
        await db.delete(transaction)
        raise ConnectorError("This authorization request expired. Please try connecting again.")

    connection = await db.get(MCPConnection, transaction.connection_id)
    if connection is None:
        await db.delete(transaction)
        raise ConnectorError("The connection this authorization belongs to no longer exists")

    data = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": settings.mcp_oauth_redirect_uri,
        "client_id": transaction.client_id,
        "code_verifier": transaction.code_verifier,
    }
    if transaction.resource:
        data["resource"] = transaction.resource

    client_secret = await crypto_service.decrypt(db, transaction.client_secret_enc)
    if client_secret:
        data["client_secret"] = client_secret

    try:
        async with httpx.AsyncClient(timeout=20.0) as http:
            resp = await http.post(
                transaction.token_endpoint,
                data=data,
                headers={"Accept": "application/json"},
            )
    except httpx.HTTPError as exc:
        connection.status = ConnectionStatus.ERROR
        connection.last_error = f"Could not reach the token endpoint: {exc}"
        await db.delete(transaction)
        raise ConnectorError(connection.last_error) from exc

    if resp.status_code != 200:
        detail = _describe_token_error(resp)
        connection.status = ConnectionStatus.ERROR
        connection.last_error = detail
        await db.delete(transaction)
        raise ConnectorError(detail)

    payload = resp.json()

    from mcp.shared.auth import OAuthToken

    storage = EncryptedTokenStorage(db, connection.id)
    await storage.set_tokens(
        OAuthToken(
            access_token=payload["access_token"],
            token_type=payload.get("token_type", "Bearer"),
            expires_in=payload.get("expires_in"),
            refresh_token=payload.get("refresh_token"),
            scope=payload.get("scope") or " ".join(transaction.scopes),
        )
    )

    connection.status = ConnectionStatus.CONNECTED
    connection.remote_client_id = transaction.client_id
    connection.last_connected_at = datetime.now(UTC)
    connection.last_error = None
    connection.is_enabled = True

    await db.delete(transaction)
    await db.flush()
    return connection


async def set_enabled(
    db: AsyncSession, user: User, connection_id: uuid.UUID, enabled: bool
) -> MCPConnection:
    """Toggle tool visibility without touching credentials.

    This is the "enable/disable without disconnecting" behaviour: tokens stay
    encrypted in place, so re-enabling needs no new authorization.
    """
    connection = await get_connection(db, user, connection_id)
    connection.is_enabled = enabled
    await db.flush()
    return connection


async def set_enabled_tools(
    db: AsyncSession,
    user: User,
    connection_id: uuid.UUID,
    tool_names: list[str] | None,
) -> MCPConnection:
    """Restrict which of a connector's tools reach the LLM."""
    connection = await get_connection(db, user, connection_id)
    connection.enabled_tools = tool_names
    await db.flush()
    return connection


async def disconnect(
    db: AsyncSession, user: User, connection_id: uuid.UUID
) -> MCPConnection:
    """Revoke and delete stored tokens, keeping the connection record."""
    connection = await get_connection(db, user, connection_id)

    await db.execute(
        delete(MCPCredential).where(MCPCredential.connection_id == connection.id)
    )
    await db.execute(
        delete(OAuthTransaction).where(OAuthTransaction.connection_id == connection.id)
    )

    connection.status = ConnectionStatus.DISCONNECTED
    connection.tools_cache = None
    connection.tools_synced_at = None
    connection.remote_client_id = None
    connection.last_error = None
    await db.flush()
    return connection


async def delete_connection(db: AsyncSession, user: User, connection_id: uuid.UUID) -> None:
    """Remove the connection entirely, cascading to its credentials."""
    connection = await get_connection(db, user, connection_id)
    await db.delete(connection)
    await db.flush()


def _describe_token_error(resp: httpx.Response) -> str:
    try:
        payload = resp.json()
        error = payload.get("error", "unknown_error")
        description = payload.get("error_description", "")
        return (
            f"The authorization server rejected the token request ({error}). {description}"
        ).strip()
    except ValueError:
        return f"The authorization server rejected the token request (HTTP {resp.status_code})."
