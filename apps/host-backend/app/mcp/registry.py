"""Resolves which MCP tools the LLM may use for a given user.

The filter chain is deliberately explicit, because "enabled" means several
independent things:

* the connector itself is enabled globally (admin kill switch)
* the user's connection is CONNECTED (tokens present and valid)
* the user has the connection enabled (per-user toggle, keeps tokens)
* the individual tool is in the connection's allow list, when one is set
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.mcp.client_manager import ToolDescriptor
from app.models import ConnectionStatus, MCPConnection, MCPConnector


@dataclass(slots=True)
class ResolvedTool:
    """A tool offered to the LLM, with the routing info to invoke it."""

    descriptor: ToolDescriptor
    connection: MCPConnection


async def get_active_connections(
    db: AsyncSession, user_id: uuid.UUID
) -> list[MCPConnection]:
    """Connections whose tools should currently be visible to the LLM."""
    stmt = (
        select(MCPConnection)
        .join(MCPConnector)
        .where(
            MCPConnection.user_id == user_id,
            MCPConnection.is_enabled.is_(True),
            MCPConnection.status == ConnectionStatus.CONNECTED,
            MCPConnector.is_enabled.is_(True),
        )
        .options(selectinload(MCPConnection.connector))
    )
    return list(await db.scalars(stmt))


async def resolve_tools(db: AsyncSession, user_id: uuid.UUID) -> list[ResolvedTool]:
    """Build the tool list for a chat turn from cached catalogues.

    Uses `tools_cache` rather than live `list_tools` calls so starting a chat
    does not fan out a network request per connector.
    """
    resolved: list[ResolvedTool] = []

    for connection in await get_active_connections(db, user_id):
        for entry in connection.tools_cache or []:
            name = entry.get("name")
            if not name:
                continue
            # An empty/absent allow list means every tool is permitted.
            if connection.enabled_tools and name not in connection.enabled_tools:
                continue

            resolved.append(
                ResolvedTool(
                    descriptor=ToolDescriptor(
                        name=name,
                        description=entry.get("description", ""),
                        input_schema=entry.get("input_schema")
                        or {"type": "object", "properties": {}},
                        connection_id=connection.id,
                        connector_slug=connection.connector.slug,
                        connector_name=connection.connector.name,
                    ),
                    connection=connection,
                )
            )

    return resolved


def to_openai_tools(tools: list[ResolvedTool]) -> list[dict]:
    """Render resolved tools as Responses API function-tool definitions."""
    return [
        {
            "type": "function",
            "name": tool.descriptor.qualified_name,
            "description": (
                f"[{tool.descriptor.connector_name}] {tool.descriptor.description}"
            ).strip(),
            "parameters": _ensure_object_schema(tool.descriptor.input_schema),
        }
        for tool in tools
    ]


def _ensure_object_schema(schema: dict) -> dict:
    """Guarantee a JSON-Schema object, which the tools API requires."""
    if not schema or schema.get("type") != "object":
        return {"type": "object", "properties": {}, "additionalProperties": True}
    return schema


def index_by_qualified_name(tools: list[ResolvedTool]) -> dict[str, ResolvedTool]:
    return {tool.descriptor.qualified_name: tool for tool in tools}
