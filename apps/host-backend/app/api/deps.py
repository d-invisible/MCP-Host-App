"""Shared FastAPI dependencies."""

from __future__ import annotations

from typing import Annotated

from fastapi import Cookie, Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.models import User
from app.services.auth_service import AuthError, get_user_from_access_token

# auto_error=False so we can also accept the session cookie.
_bearer = HTTPBearer(auto_error=False)

DbSession = Annotated[AsyncSession, Depends(get_db)]


async def get_current_user(
    request: Request,
    db: DbSession,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)] = None,
    access_token: Annotated[str | None, Cookie(alias="mcp_host_access")] = None,
) -> User:
    """Resolve the caller from either a Bearer header or the session cookie.

    The cookie path is what the browser UI uses; the header path is for API
    clients and for OAuth flows that redirect back into the app.
    """
    token = credentials.credentials if credentials is not None else access_token
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )

    try:
        return await get_user_from_access_token(db, token)
    except AuthError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc),
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc


async def get_optional_user(
    request: Request,
    db: DbSession,
    access_token: Annotated[str | None, Cookie(alias="mcp_host_access")] = None,
) -> User | None:
    """Like `get_current_user`, but returns None instead of raising.

    Used by the OAuth authorize endpoint, which must redirect an anonymous
    caller to the login page rather than returning a 401.
    """
    if not access_token:
        return None
    try:
        return await get_user_from_access_token(db, access_token)
    except AuthError:
        return None


CurrentUser = Annotated[User, Depends(get_current_user)]
OptionalUser = Annotated[User | None, Depends(get_optional_user)]
