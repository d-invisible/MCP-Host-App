"""Refresh-token rotation and reuse detection.

These run against a live backend on :8000, because the behaviour under test is
transactional: reuse detection must *commit* its revocation even though the
request fails, and that only shows up through the real dependency stack.
"""

from __future__ import annotations

import os
import random
import re

import httpx
import pytest

# Point at a different instance with TEST_BACKEND_URL, e.g. when running the
# suite against a container or a staging deployment.
BASE = os.getenv("TEST_BACKEND_URL", "http://127.0.0.1:8000")
REFRESH_COOKIE = "mcp_host_refresh"


def _backend_running() -> bool:
    try:
        return httpx.get(f"{BASE}/health", timeout=3.0).status_code == 200
    except httpx.HTTPError:
        return False


pytestmark = pytest.mark.skipif(
    not _backend_running(), reason="requires the backend running on :8000"
)


def _refresh_cookie(response: httpx.Response) -> str:
    """Pull the refresh cookie out of Set-Cookie.

    Read from the header rather than `response.cookies`, because a 401 that
    clears the cookie leaves nothing in the jar to read.
    """
    for header in response.headers.get_list("set-cookie"):
        match = re.match(rf"{REFRESH_COOKIE}=([^;]+)", header)
        if match and match.group(1):
            return match.group(1)
    raise AssertionError(f"no {REFRESH_COOKIE} in response headers")


def _register() -> str:
    """Create a throwaway account and return its refresh token."""
    email = f"test{random.randint(10_000, 999_999)}@example.com"
    with httpx.Client(base_url=BASE, timeout=30.0) as client:
        response = client.post(
            "/api/auth/register", json={"email": email, "password": "testpass12345"}
        )
        response.raise_for_status()
        return _refresh_cookie(response)


def _refresh_with(token: str) -> httpx.Response:
    """POST /refresh presenting exactly `token`, and nothing else.

    Each call gets its own client so the jar cannot carry a cookie between
    calls: the server issues a replacement on every successful refresh, which
    would otherwise overwrite the token a test is deliberately replaying.

    The cookie is set on the jar rather than passed per-request (per-request
    cookies are deprecated in httpx), with domain and path left unset — pinning
    them makes httpx's matching drop the cookie.
    """
    with httpx.Client(base_url=BASE, timeout=30.0) as client:
        client.cookies.set(REFRESH_COOKIE, token)
        return client.post("/api/auth/refresh")


def test_refresh_rotates_the_token():
    first = _register()

    response = _refresh_with(first)
    assert response.status_code == 200
    assert _refresh_cookie(response) != first, "each refresh must issue a new token"


def test_replaying_a_rotated_token_is_rejected():
    first = _register()
    assert _refresh_with(first).status_code == 200

    replay = _refresh_with(first)
    assert replay.status_code == 401
    assert "already been used" in replay.json()["detail"]


def test_reuse_revokes_the_whole_family():
    """The security-critical case.

    Replaying an old token means it leaked, so the token the *attacker* never
    had must die too. This regressed once: the revocation was written and then
    rolled back, because the request raised. It must be committed explicitly.
    """
    first = _register()
    second = _refresh_cookie(_refresh_with(first))

    # Attacker replays the stolen, already-rotated token.
    assert _refresh_with(first).status_code == 401

    # The legitimate user's current token must now be dead as well.
    response = _refresh_with(second)
    assert response.status_code == 401, (
        "family revocation did not persist; the rollback likely undid it"
    )


def test_access_token_is_required_for_protected_routes():
    with httpx.Client(base_url=BASE, timeout=30.0) as client:
        assert client.get("/api/auth/me").status_code == 401
        assert client.get("/api/connectors").status_code == 401
