"""Tests for the authorization server's redirect and error handling."""

from __future__ import annotations

import pytest

from app.mcp.client_manager import _root_cause
from app.models import OAuthClient
from app.services.oauth_server_service import (
    OAuthError,
    build_redirect,
    validate_redirect_uri,
)


def _client(*uris: str) -> OAuthClient:
    return OAuthClient(
        client_id="c1",
        client_name="Test",
        redirect_uris=list(uris),
        scopes=["chat"],
        grant_types=["authorization_code"],
    )


def test_redirect_uri_must_match_exactly():
    client = _client("https://app.example.com/callback")
    validate_redirect_uri(client, "https://app.example.com/callback")

    # Prefix/suffix variations must all be rejected: accepting them is the
    # classic open-redirect token-theft hole.
    for bad in [
        "https://app.example.com/callback/evil",
        "https://app.example.com/callback?x=1",
        "https://evil.com/callback",
        "https://app.example.com",
    ]:
        with pytest.raises(OAuthError):
            validate_redirect_uri(client, bad)


def test_build_redirect_appends_query():
    assert (
        build_redirect("https://app.example.com/cb", {"code": "abc"})
        == "https://app.example.com/cb?code=abc"
    )


def test_build_redirect_preserves_existing_query():
    result = build_redirect("https://app.example.com/cb?a=1", {"code": "abc"})
    assert result.startswith("https://app.example.com/cb?a=1&")
    assert "code=abc" in result


def test_build_redirect_encodes_values():
    result = build_redirect("https://app.example.com/cb", {"state": "a b&c"})
    assert " " not in result.split("?", 1)[1]


def test_root_cause_unwraps_exception_group():
    """Transport failures arrive wrapped; the user needs the inner message."""
    group = ExceptionGroup("unhandled errors in a TaskGroup", [ConnectionRefusedError("refused")])
    assert "refused" in _root_cause(group)
    assert "TaskGroup" not in _root_cause(group)


def test_root_cause_handles_plain_exception():
    assert _root_cause(ValueError("bad value")) == "bad value"


def test_root_cause_falls_back_to_class_name():
    assert _root_cause(ValueError()) == "ValueError"
