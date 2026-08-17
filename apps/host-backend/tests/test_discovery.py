"""Tests for MCP OAuth discovery."""

from __future__ import annotations

from app.mcp.discovery import _wellknown_resource_url


def test_wellknown_resource_url_preserves_path():
    """RFC 9728 inserts the resource path after the well-known segment."""
    assert (
        _wellknown_resource_url("https://api.example.com/mcp")
        == "https://api.example.com/.well-known/oauth-protected-resource/mcp"
    )


def test_wellknown_resource_url_with_nested_path():
    assert (
        _wellknown_resource_url("https://api.example.com/servers/notes/mcp")
        == "https://api.example.com/.well-known/oauth-protected-resource/servers/notes/mcp"
    )


def test_wellknown_resource_url_root():
    assert (
        _wellknown_resource_url("https://api.example.com/")
        == "https://api.example.com/.well-known/oauth-protected-resource"
    )


def test_wellknown_resource_url_keeps_port():
    assert (
        _wellknown_resource_url("http://localhost:8101/mcp")
        == "http://localhost:8101/.well-known/oauth-protected-resource/mcp"
    )
