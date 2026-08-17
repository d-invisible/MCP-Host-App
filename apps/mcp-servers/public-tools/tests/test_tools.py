"""Tests for the Public Tools server.

Tools are exercised through an in-memory MCP client rather than by calling the
Python functions directly, so the test covers what a real host sees: schema
generation, argument coercion, and content blocks.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from mcp import Client

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from server import mcp  # noqa: E402


async def call(name: str, arguments: dict) -> str:
    """Invoke a tool over an in-memory session and return its text."""
    async with Client(mcp) as client:
        result = await client.call_tool(name, arguments)
        return "\n".join(
            getattr(block, "text", str(block)) for block in result.content
        ).strip()


async def test_tools_are_advertised():
    async with Client(mcp) as client:
        result = await client.list_tools()

    names = {tool.name for tool in result.tools}
    assert names == {"current_time", "convert_units", "hash_text"}

    # A host renders these into LLM tool definitions, so both must be present.
    for tool in result.tools:
        assert tool.description, f"{tool.name} has no description"
        assert tool.input_schema["type"] == "object"


async def test_current_time_defaults_to_utc():
    assert "UTC" in await call("current_time", {})


async def test_current_time_accepts_a_timezone():
    result = await call("current_time", {"timezone": "Asia/Kolkata"})
    assert "+0530" in result


async def test_current_time_reports_unknown_timezone_as_content():
    """An invalid argument must come back as readable text, not an exception.

    The model reads this and retries; a raised error would just fail the call.
    """
    result = await call("current_time", {"timezone": "Mars/Olympus"})
    assert "Unknown timezone" in result


@pytest.mark.parametrize(
    ("value", "source", "target", "expected"),
    [
        (1, "km", "m", "1000"),
        (1, "mi", "km", "1.60934"),
        (1, "kg", "lb", "2.20462"),
        (100, "c", "f", "212"),
        (0, "c", "k", "273.15"),
        (32, "f", "c", "0"),
    ],
)
async def test_convert_units(value, source, target, expected):
    result = await call(
        "convert_units", {"value": value, "from_unit": source, "to_unit": target}
    )
    assert expected in result


async def test_convert_units_rejects_mismatched_families():
    result = await call("convert_units", {"value": 1, "from_unit": "kg", "to_unit": "km"})
    assert "Cannot convert" in result


async def test_hash_text():
    result = await call("hash_text", {"text": "hello"})
    # Known SHA-256 of "hello".
    assert "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824" in result


async def test_hash_text_rejects_unknown_algorithm():
    result = await call("hash_text", {"text": "hello", "algorithm": "rot13"})
    assert "Unsupported algorithm" in result
