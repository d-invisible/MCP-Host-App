"""Request/response models for connector endpoints."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models import AuthKind, ConnectionStatus


class ToolInfo(BaseModel):
    name: str
    description: str = ""
    input_schema: dict = Field(default_factory=dict)
    enabled: bool = True


class ConnectionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    status: ConnectionStatus
    is_enabled: bool
    tools_synced_at: datetime | None = None
    last_connected_at: datetime | None = None
    last_error: str | None = None
    tools: list[ToolInfo] = Field(default_factory=list)


class ConnectorOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    slug: str
    name: str
    description: str | None = None
    server_url: str
    auth_kind: AuthKind
    icon: str | None = None
    is_builtin: bool
    is_enabled: bool
    connection: ConnectionOut | None = None


class CreateConnectorRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    server_url: str = Field(min_length=1)
    description: str | None = Field(default=None, max_length=1000)
    auth_kind: AuthKind = AuthKind.OAUTH
    scopes: list[str] = Field(default_factory=list)
    static_client_id: str | None = None


class ConnectResponse(BaseModel):
    connection_id: uuid.UUID
    status: ConnectionStatus
    # Present when the browser must be redirected to an authorization server.
    authorization_url: str | None = None


class SetEnabledRequest(BaseModel):
    enabled: bool


class SetEnabledToolsRequest(BaseModel):
    # None or an empty list means "all tools allowed".
    tool_names: list[str] | None = None
