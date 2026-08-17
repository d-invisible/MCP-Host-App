"""Tests for tool naming and schema rendering."""

from __future__ import annotations

import uuid

from app.mcp.client_manager import ToolDescriptor
from app.mcp.registry import (
    ResolvedTool,
    _ensure_object_schema,
    index_by_qualified_name,
    to_openai_tools,
)


def _descriptor(name: str, slug: str = "notes") -> ToolDescriptor:
    return ToolDescriptor(
        name=name,
        description="Does a thing",
        input_schema={"type": "object", "properties": {"q": {"type": "string"}}},
        connection_id=uuid.uuid4(),
        connector_slug=slug,
        connector_name=slug.title(),
    )


def test_qualified_name_namespaces_by_connector():
    """Same tool name on two servers must not collide."""
    a = _descriptor("search", "notes")
    b = _descriptor("search", "crm")

    assert a.qualified_name == "notes__search"
    assert b.qualified_name == "crm__search"
    assert a.qualified_name != b.qualified_name


def test_to_openai_tools_shape():
    class _Conn:
        id = uuid.uuid4()

    tools = [ResolvedTool(descriptor=_descriptor("search"), connection=_Conn())]
    rendered = to_openai_tools(tools)

    assert len(rendered) == 1
    assert rendered[0]["type"] == "function"
    assert rendered[0]["name"] == "notes__search"
    # The connector name is prefixed so the model knows where a tool comes from.
    assert "[Notes]" in rendered[0]["description"]
    assert rendered[0]["parameters"]["type"] == "object"


def test_ensure_object_schema_repairs_invalid_schema():
    assert _ensure_object_schema({})["type"] == "object"
    assert _ensure_object_schema({"type": "string"})["type"] == "object"

    valid = {"type": "object", "properties": {"a": {"type": "number"}}}
    assert _ensure_object_schema(valid) == valid


def test_index_by_qualified_name():
    class _Conn:
        id = uuid.uuid4()

    tools = [
        ResolvedTool(descriptor=_descriptor("search", "notes"), connection=_Conn()),
        ResolvedTool(descriptor=_descriptor("search", "crm"), connection=_Conn()),
    ]
    index = index_by_qualified_name(tools)

    assert set(index) == {"notes__search", "crm__search"}
