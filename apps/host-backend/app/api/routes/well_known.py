"""Discovery documents.

Two roles are served from this one app:

* **Authorization server** (RFC 8414) — for MCP-2, which forwards auth to us.
* **OAuth client** (CIMD) — `/.well-known/oauth-client` publishes a Client ID
  Metadata Document. Because the document is fetchable at an https URL, remote
  MCP authorization servers can identify this host app by URL alone, with no
  dynamic registration and no shared secret.
"""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app.core.config import settings

router = APIRouter(tags=["discovery"])

SUPPORTED_SCOPES = ["chat", "connectors", "profile", "offline_access"]


@router.get("/.well-known/oauth-authorization-server")
async def authorization_server_metadata() -> JSONResponse:
    """RFC 8414 authorization server metadata."""
    base = settings.issuer_url
    return JSONResponse(
        {
            "issuer": base,
            "authorization_endpoint": f"{base}/oauth/authorize",
            "token_endpoint": f"{base}/oauth/token",
            "registration_endpoint": f"{base}/oauth/register",
            "revocation_endpoint": f"{base}/oauth/revoke",
            "jwks_uri": f"{base}/.well-known/jwks.json",
            "userinfo_endpoint": f"{base}/oauth/userinfo",
            "scopes_supported": SUPPORTED_SCOPES,
            "response_types_supported": ["code"],
            "grant_types_supported": ["authorization_code", "refresh_token"],
            "token_endpoint_auth_methods_supported": ["none", "client_secret_post"],
            # PKCE is mandatory: OAuth 2.1 and the MCP spec both require S256.
            "code_challenge_methods_supported": ["S256"],
            # RFC 8707 — lets a client bind a token to a specific MCP server.
            "resource_indicators_supported": True,
            "client_id_metadata_document_supported": True,
            "service_documentation": f"{settings.frontend_base_url}/docs",
        },
        headers={"Cache-Control": "public, max-age=3600"},
    )


@router.get("/.well-known/openid-configuration")
async def openid_configuration() -> JSONResponse:
    """Alias so OIDC-aware clients discover the same endpoints."""
    return await authorization_server_metadata()


@router.get("/.well-known/oauth-client")
async def client_id_metadata_document() -> JSONResponse:
    """CIMD — this app's identity as an OAuth *client*.

    The URL of this document is the `client_id` we present to remote MCP
    authorization servers.
    """
    return JSONResponse(
        {
            "client_id": settings.cimd_url,
            "client_name": settings.app_name,
            "client_uri": settings.frontend_base_url,
            "logo_uri": f"{settings.frontend_base_url}/icon.png",
            "redirect_uris": [settings.mcp_oauth_redirect_uri],
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"],
            # Public client: no secret, PKCE only.
            "token_endpoint_auth_method": "none",
            "scope": "openid profile email offline_access",
            "software_id": "mcp-host-web-app",
            "software_version": "0.1.0",
        },
        headers={"Cache-Control": "public, max-age=3600"},
    )


@router.get("/.well-known/jwks.json")
async def jwks() -> JSONResponse:
    """JWKS endpoint.

    Day 1 signs with HS256 (symmetric), which has no publishable public key, so
    this returns an empty key set. Switching to RS256/ES256 for cross-service
    verification is a Day 2 concern once the MCP servers need to validate our
    tokens without sharing the secret.
    """
    return JSONResponse({"keys": []}, headers={"Cache-Control": "public, max-age=300"})
