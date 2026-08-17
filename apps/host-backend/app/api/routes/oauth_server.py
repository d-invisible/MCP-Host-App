"""OAuth 2.1 authorization server endpoints.

MCP-2 delegates its authentication here, so these endpoints are what a remote
MCP server's clients hit when they need a token scoped to this app's users.
"""

from __future__ import annotations

from typing import Annotated
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, Form, HTTPException, Query, Request
from fastapi.responses import JSONResponse, RedirectResponse

from app.api.deps import DbSession, OptionalUser
from app.core.config import settings
from app.core.security import decode_token
from app.schemas.auth import UserOut
from app.services import oauth_server_service as oauth
from app.services.oauth_server_service import OAuthError

router = APIRouter(tags=["oauth-server"])


def _oauth_error_response(exc: OAuthError) -> JSONResponse:
    return JSONResponse(
        {"error": exc.error, "error_description": exc.description},
        status_code=exc.status_code,
    )


@router.get("/oauth/authorize")
async def authorize(
    request: Request,
    db: DbSession,
    user: OptionalUser,
    client_id: Annotated[str, Query()],
    redirect_uri: Annotated[str, Query()],
    response_type: Annotated[str, Query()] = "code",
    scope: Annotated[str, Query()] = "chat",
    state: Annotated[str | None, Query()] = None,
    code_challenge: Annotated[str | None, Query()] = None,
    code_challenge_method: Annotated[str, Query()] = "S256",
    resource: Annotated[str | None, Query()] = None,
):
    """Authorization endpoint.

    An unauthenticated caller is bounced to the frontend login page with the
    full query string preserved, so the flow resumes after sign-in.
    """
    if response_type != "code":
        return _oauth_error_response(
            OAuthError("unsupported_response_type", "Only response_type=code is supported")
        )
    if not code_challenge:
        return _oauth_error_response(
            OAuthError("invalid_request", "PKCE code_challenge is required")
        )

    # A CIMD client_id is an https URL: fetch and trust-on-first-use.
    try:
        if client_id.startswith("https://") or (
            settings.is_local and client_id.startswith("http://")
        ):
            client = await _resolve_cimd_client(db, client_id)
        else:
            client = await oauth.get_client(db, client_id)
        oauth.validate_redirect_uri(client, redirect_uri)
    except OAuthError as exc:
        return _oauth_error_response(exc)

    if user is None:
        resume_url = f"{settings.backend_base_url}/oauth/authorize?{request.url.query}"
        login_url = (
            f"{settings.frontend_base_url}/login?" + urlencode({"next": resume_url})
        )
        return RedirectResponse(login_url, status_code=302)

    # Trusted first-party clients skip the consent screen; everyone else is
    # sent to the frontend to approve the requested scopes.
    if not client.is_trusted:
        consent_url = f"{settings.frontend_base_url}/oauth/consent?{request.url.query}"
        return RedirectResponse(consent_url, status_code=302)

    return await _issue_code_redirect(
        db,
        client=client,
        user=user,
        redirect_uri=redirect_uri,
        scope=scope,
        state=state,
        code_challenge=code_challenge,
        code_challenge_method=code_challenge_method,
        resource=resource,
    )


@router.post("/oauth/authorize/consent")
async def authorize_consent(
    db: DbSession,
    user: OptionalUser,
    client_id: Annotated[str, Form()],
    redirect_uri: Annotated[str, Form()],
    code_challenge: Annotated[str, Form()],
    scope: Annotated[str, Form()] = "chat",
    state: Annotated[str | None, Form()] = None,
    code_challenge_method: Annotated[str, Form()] = "S256",
    resource: Annotated[str | None, Form()] = None,
    approved: Annotated[bool, Form()] = False,
):
    """Called by the frontend consent screen once the user decides."""
    if user is None:
        raise HTTPException(401, "Not authenticated")

    if not approved:
        return JSONResponse(
            {
                "redirect_to": oauth.build_redirect(
                    redirect_uri,
                    {"error": "access_denied", **({"state": state} if state else {})},
                )
            }
        )

    try:
        client = await oauth.get_client(db, client_id)
        oauth.validate_redirect_uri(client, redirect_uri)
        code = await oauth.create_authorization_code(
            db,
            client=client,
            user=user,
            redirect_uri=redirect_uri,
            scopes=scope.split(),
            code_challenge=code_challenge,
            code_challenge_method=code_challenge_method,
            resource=resource,
        )
    except OAuthError as exc:
        return _oauth_error_response(exc)

    params = {"code": code}
    if state:
        params["state"] = state
    return JSONResponse({"redirect_to": oauth.build_redirect(redirect_uri, params)})


@router.post("/oauth/token")
async def token(
    db: DbSession,
    grant_type: Annotated[str, Form()],
    client_id: Annotated[str, Form()],
    code: Annotated[str | None, Form()] = None,
    redirect_uri: Annotated[str | None, Form()] = None,
    code_verifier: Annotated[str | None, Form()] = None,
    refresh_token: Annotated[str | None, Form()] = None,
    resource: Annotated[str | None, Form()] = None,
):
    """Token endpoint: authorization_code and refresh_token grants."""
    try:
        if grant_type == "authorization_code":
            if not (code and redirect_uri and code_verifier):
                raise OAuthError(
                    "invalid_request",
                    "code, redirect_uri and code_verifier are all required",
                )
            result = await oauth.exchange_authorization_code(
                db,
                code=code,
                client_id=client_id,
                redirect_uri=redirect_uri,
                code_verifier=code_verifier,
                resource=resource,
            )
        elif grant_type == "refresh_token":
            if not refresh_token:
                raise OAuthError("invalid_request", "refresh_token is required")
            result = await oauth.refresh_access_token(
                db, refresh_token=refresh_token, client_id=client_id
            )
        else:
            raise OAuthError("unsupported_grant_type", f"Unsupported grant_type: {grant_type}")
    except OAuthError as exc:
        return _oauth_error_response(exc)

    return JSONResponse(result, headers={"Cache-Control": "no-store", "Pragma": "no-cache"})


@router.post("/oauth/register")
async def register_client(db: DbSession, request: Request):
    """RFC 7591 dynamic client registration."""
    body = await request.json()
    try:
        client, _ = await oauth.register_client(
            db,
            client_name=body.get("client_name", "Unnamed MCP client"),
            redirect_uris=body.get("redirect_uris", []),
            scopes=(body.get("scope") or "").split() or None,
            grant_types=body.get("grant_types"),
        )
    except OAuthError as exc:
        return _oauth_error_response(exc)

    return JSONResponse(
        {
            "client_id": client.client_id,
            "client_name": client.client_name,
            "redirect_uris": client.redirect_uris,
            "grant_types": client.grant_types,
            "response_types": ["code"],
            "token_endpoint_auth_method": "none",
            "scope": " ".join(client.scopes),
        },
        status_code=201,
    )


@router.get("/oauth/userinfo")
async def userinfo(db: DbSession, request: Request):
    """Minimal OIDC-style userinfo, resolved from the Bearer access token."""
    header = request.headers.get("authorization", "")
    if not header.lower().startswith("bearer "):
        raise HTTPException(401, "Missing bearer token")

    from app.models import User

    try:
        payload = decode_token(header.split(" ", 1)[1], expected_type="access")
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(401, "Invalid access token") from exc

    import uuid as _uuid

    user = await db.get(User, _uuid.UUID(payload["sub"]))
    if user is None:
        raise HTTPException(401, "Unknown subject")

    return JSONResponse(
        {
            "sub": str(user.id),
            "email": user.email,
            "name": user.display_name,
            **UserOut.model_validate(user).model_dump(mode="json", exclude={"id"}),
        }
    )


async def _resolve_cimd_client(db: DbSession, client_id_url: str):
    """Fetch and cache a Client ID Metadata Document."""
    async with httpx.AsyncClient(timeout=10.0, follow_redirects=False) as http:
        try:
            resp = await http.get(client_id_url, headers={"Accept": "application/json"})
            resp.raise_for_status()
            metadata = resp.json()
        except Exception as exc:  # noqa: BLE001
            raise OAuthError(
                "invalid_client",
                f"Could not fetch client metadata document at {client_id_url}",
            ) from exc

    # The document must claim the same URL it was fetched from, otherwise any
    # site could publish a document impersonating another client.
    if metadata.get("client_id") not in (None, client_id_url):
        raise OAuthError("invalid_client", "client_id in metadata does not match its URL")

    return await oauth.upsert_cimd_client(db, client_id_url=client_id_url, metadata=metadata)


async def _issue_code_redirect(
    db,
    *,
    client,
    user,
    redirect_uri: str,
    scope: str,
    state: str | None,
    code_challenge: str,
    code_challenge_method: str,
    resource: str | None,
) -> RedirectResponse:
    code = await oauth.create_authorization_code(
        db,
        client=client,
        user=user,
        redirect_uri=redirect_uri,
        scopes=scope.split(),
        code_challenge=code_challenge,
        code_challenge_method=code_challenge_method,
        resource=resource,
    )
    params = {"code": code}
    if state:
        params["state"] = state
    return RedirectResponse(oauth.build_redirect(redirect_uri, params), status_code=302)
