"""OAuth discovery for remote MCP servers.

Implements the chain the MCP authorization spec expects:

1. Call the MCP endpoint unauthenticated; a 401 carries a `WWW-Authenticate`
   header pointing at the protected-resource metadata (RFC 9728).
2. Fetch that metadata to learn which authorization server(s) to use.
3. Fetch the authorization server metadata (RFC 8414) for the real endpoints.

Each step falls back to well-known path probing when a server omits a hint.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from urllib.parse import urlparse

import httpx

_WWW_AUTH_RESOURCE = re.compile(r'resource_metadata="([^"]+)"', re.IGNORECASE)


@dataclass(slots=True)
class DiscoveredAuth:
    """Everything needed to start an authorization-code flow."""

    authorization_endpoint: str
    token_endpoint: str
    issuer: str
    registration_endpoint: str | None = None
    resource: str | None = None
    scopes_supported: list[str] = field(default_factory=list)
    supports_cimd: bool = False


class DiscoveryError(Exception):
    """Raised when a server does not expose usable OAuth metadata."""


async def discover(server_url: str, *, timeout: float = 10.0) -> DiscoveredAuth:
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as http:
        resource_metadata_url = await _probe_resource_metadata_url(http, server_url)

        resource: str | None = None
        auth_server_urls: list[str] = []
        scopes: list[str] = []

        if resource_metadata_url:
            metadata = await _get_json(http, resource_metadata_url)
            if metadata:
                resource = metadata.get("resource")
                auth_server_urls = metadata.get("authorization_servers") or []
                scopes = metadata.get("scopes_supported") or []

        # Fall back to treating the MCP server's own origin as the AS.
        if not auth_server_urls:
            parsed = urlparse(server_url)
            auth_server_urls = [f"{parsed.scheme}://{parsed.netloc}"]

        for issuer in auth_server_urls:
            as_metadata = await _fetch_as_metadata(http, issuer)
            if as_metadata is None:
                continue
            return DiscoveredAuth(
                authorization_endpoint=as_metadata["authorization_endpoint"],
                token_endpoint=as_metadata["token_endpoint"],
                issuer=as_metadata.get("issuer", issuer),
                registration_endpoint=as_metadata.get("registration_endpoint"),
                resource=resource or server_url,
                scopes_supported=as_metadata.get("scopes_supported") or scopes,
                supports_cimd=bool(as_metadata.get("client_id_metadata_document_supported")),
            )

    raise DiscoveryError(
        f"No usable OAuth authorization server metadata found for {server_url}"
    )


async def _probe_resource_metadata_url(http: httpx.AsyncClient, server_url: str) -> str | None:
    """Find the RFC 9728 protected-resource metadata URL."""
    try:
        resp = await http.post(
            server_url,
            json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
            headers={"Accept": "application/json, text/event-stream"},
        )
    except httpx.HTTPError:
        return _wellknown_resource_url(server_url)

    if resp.status_code == 401:
        match = _WWW_AUTH_RESOURCE.search(resp.headers.get("www-authenticate", ""))
        if match:
            return match.group(1)
    return _wellknown_resource_url(server_url)


def _wellknown_resource_url(server_url: str) -> str:
    """Build `/.well-known/oauth-protected-resource<path>` for the server URL."""
    parsed = urlparse(server_url)
    path = parsed.path.rstrip("/")
    return f"{parsed.scheme}://{parsed.netloc}/.well-known/oauth-protected-resource{path}"


async def _fetch_as_metadata(http: httpx.AsyncClient, issuer: str) -> dict | None:
    """Try each well-known location an authorization server may publish at."""
    base = issuer.rstrip("/")
    parsed = urlparse(base)
    origin = f"{parsed.scheme}://{parsed.netloc}"
    path = parsed.path.rstrip("/")

    candidates = [
        f"{origin}/.well-known/oauth-authorization-server{path}",
        f"{base}/.well-known/oauth-authorization-server",
        f"{origin}/.well-known/openid-configuration{path}",
        f"{base}/.well-known/openid-configuration",
    ]
    for url in dict.fromkeys(candidates):
        metadata = await _get_json(http, url)
        if metadata and "authorization_endpoint" in metadata and "token_endpoint" in metadata:
            return metadata
    return None


async def _get_json(http: httpx.AsyncClient, url: str) -> dict | None:
    try:
        resp = await http.get(url, headers={"Accept": "application/json"})
        if resp.status_code == 200:
            return resp.json()
    except (httpx.HTTPError, ValueError):
        return None
    return None
