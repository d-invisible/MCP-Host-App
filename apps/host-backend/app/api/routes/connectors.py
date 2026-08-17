"""Connector catalogue and connection management endpoints."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, status
from fastapi.responses import RedirectResponse
from sqlalchemy import select

from app.api.deps import CurrentUser, DbSession
from app.core.config import settings
from app.mcp.client_manager import MCPClientManager, MCPConnectionError
from app.models import MCPConnector
from app.schemas.connector import (
    ConnectionOut,
    ConnectorOut,
    ConnectResponse,
    CreateConnectorRequest,
    SetEnabledRequest,
    SetEnabledToolsRequest,
    ToolInfo,
)
from app.services import connector_service
from app.services.connector_service import ConnectorError

router = APIRouter(prefix="/api/connectors", tags=["connectors"])


def _to_connection_out(connection) -> ConnectionOut:
    """Merge the cached tool catalogue with the per-tool allow list."""
    allow = set(connection.enabled_tools or [])
    tools = [
        ToolInfo(
            name=entry.get("name", ""),
            description=entry.get("description", "") or "",
            input_schema=entry.get("input_schema") or {},
            enabled=(not allow) or entry.get("name") in allow,
        )
        for entry in (connection.tools_cache or [])
    ]
    out = ConnectionOut.model_validate(connection)
    out.tools = tools
    return out


@router.get("", response_model=list[ConnectorOut])
async def list_connectors(user: CurrentUser, db: DbSession):
    pairs = await connector_service.list_connectors_for_user(db, user)
    results: list[ConnectorOut] = []
    for connector, connection in pairs:
        item = ConnectorOut.model_validate(connector)
        item.connection = _to_connection_out(connection) if connection else None
        results.append(item)
    return results


@router.post("", response_model=ConnectorOut, status_code=status.HTTP_201_CREATED)
async def create_connector(payload: CreateConnectorRequest, user: CurrentUser, db: DbSession):
    """Add a custom MCP server to the catalogue."""
    slug = _slugify(payload.name)
    existing = await db.scalar(select(MCPConnector).where(MCPConnector.slug == slug))
    if existing is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, f"A connector named {payload.name!r} exists")

    connector = MCPConnector(
        slug=slug,
        name=payload.name,
        description=payload.description,
        server_url=payload.server_url,
        auth_kind=payload.auth_kind,
        scopes=payload.scopes,
        static_client_id=payload.static_client_id,
        is_builtin=False,
        is_enabled=True,
    )
    db.add(connector)
    await db.flush()
    return ConnectorOut.model_validate(connector)


@router.post("/{connector_id}/connect", response_model=ConnectResponse)
async def connect(connector_id: uuid.UUID, user: CurrentUser, db: DbSession):
    """Begin connecting. Returns an authorization URL when OAuth is required."""
    try:
        connection, authorization_url = await connector_service.begin_connect(
            db, user, connector_id
        )
    except ConnectorError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc

    # A no-auth server is usable straight away, so sync its tools now.
    if authorization_url is None:
        try:
            await MCPClientManager(db).sync_tools(connection)
        except MCPConnectionError as exc:
            raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc

    return ConnectResponse(
        connection_id=connection.id,
        status=connection.status,
        authorization_url=authorization_url,
    )


@router.get("/oauth/callback")
async def oauth_callback(
    db: DbSession,
    state: Annotated[str | None, Query()] = None,
    code: Annotated[str | None, Query()] = None,
    error: Annotated[str | None, Query()] = None,
    error_description: Annotated[str | None, Query()] = None,
):
    """Redirect target for every outbound MCP authorization.

    Always redirects back into the frontend so the user lands on a real page
    rather than raw JSON.
    """
    settings_url = f"{settings.frontend_base_url}/settings/connectors"

    if error:
        detail = error_description or error
        return RedirectResponse(f"{settings_url}?error={_q(detail)}", status_code=302)
    if not (state and code):
        return RedirectResponse(
            f"{settings_url}?error={_q('The authorization response was incomplete')}",
            status_code=302,
        )

    try:
        connection = await connector_service.complete_oauth(db, state=state, code=code)
    except ConnectorError as exc:
        return RedirectResponse(f"{settings_url}?error={_q(str(exc))}", status_code=302)

    # Commit the tokens before the tool sync, so a sync failure cannot roll
    # back a successful authorization.
    await db.commit()

    try:
        await MCPClientManager(db).sync_tools(connection)
        await db.commit()
    except MCPConnectionError as exc:
        await db.commit()
        return RedirectResponse(
            f"{settings_url}?connected={_q(connection.connector.name)}&warning={_q(str(exc))}",
            status_code=302,
        )

    return RedirectResponse(
        f"{settings_url}?connected={_q(connection.connector.name)}", status_code=302
    )


@router.post("/connections/{connection_id}/enabled", response_model=ConnectionOut)
async def set_enabled(
    connection_id: uuid.UUID,
    payload: SetEnabledRequest,
    user: CurrentUser,
    db: DbSession,
):
    """Enable or disable a connection without dropping its credentials."""
    try:
        connection = await connector_service.set_enabled(
            db, user, connection_id, payload.enabled
        )
    except ConnectorError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    return _to_connection_out(connection)


@router.post("/connections/{connection_id}/tools", response_model=ConnectionOut)
async def set_enabled_tools(
    connection_id: uuid.UUID,
    payload: SetEnabledToolsRequest,
    user: CurrentUser,
    db: DbSession,
):
    try:
        connection = await connector_service.set_enabled_tools(
            db, user, connection_id, payload.tool_names
        )
    except ConnectorError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    return _to_connection_out(connection)


@router.post("/connections/{connection_id}/refresh", response_model=ConnectionOut)
async def refresh_tools(connection_id: uuid.UUID, user: CurrentUser, db: DbSession):
    """Re-query the server's tool list."""
    try:
        connection = await connector_service.get_connection(db, user, connection_id)
    except ConnectorError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc

    try:
        await MCPClientManager(db).sync_tools(connection)
    except MCPConnectionError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc

    return _to_connection_out(connection)


@router.post("/connections/{connection_id}/disconnect", response_model=ConnectionOut)
async def disconnect(connection_id: uuid.UUID, user: CurrentUser, db: DbSession):
    """Delete stored tokens but keep the connection listed."""
    try:
        connection = await connector_service.disconnect(db, user, connection_id)
    except ConnectorError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    return _to_connection_out(connection)


@router.delete("/connections/{connection_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_connection(connection_id: uuid.UUID, user: CurrentUser, db: DbSession):
    try:
        await connector_service.delete_connection(db, user, connection_id)
    except ConnectorError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc


def _q(value: str) -> str:
    from urllib.parse import quote

    return quote(value, safe="")


def _slugify(name: str) -> str:
    import re

    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return slug or "connector"
