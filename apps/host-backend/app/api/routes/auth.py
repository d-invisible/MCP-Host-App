"""Sign-up / sign-in / session endpoints for the host app's own users."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Cookie, HTTPException, Request, Response, status

from app.api.deps import CurrentUser, DbSession
from app.core.config import settings
from app.schemas.auth import LoginRequest, RegisterRequest, SessionOut, UserOut
from app.services import auth_service
from app.services.auth_service import AuthError

router = APIRouter(prefix="/api/auth", tags=["auth"])

ACCESS_COOKIE = "mcp_host_access"
REFRESH_COOKIE = "mcp_host_refresh"


def _set_session_cookies(response: Response, access_token: str, refresh_token: str) -> None:
    """Store the session in HttpOnly cookies.

    HttpOnly keeps the tokens away from any XSS payload; SameSite=lax still
    permits the top-level redirect back from an external OAuth provider.
    """
    secure = not settings.is_local
    response.set_cookie(
        ACCESS_COOKIE,
        access_token,
        max_age=settings.access_token_ttl_seconds,
        httponly=True,
        secure=secure,
        samesite="lax",
        path="/",
    )
    response.set_cookie(
        REFRESH_COOKIE,
        refresh_token,
        max_age=settings.refresh_token_ttl_seconds,
        httponly=True,
        secure=secure,
        samesite="lax",
        path="/api/auth",
    )


def _clear_session_cookies(response: Response) -> None:
    response.delete_cookie(ACCESS_COOKIE, path="/")
    response.delete_cookie(REFRESH_COOKIE, path="/api/auth")


@router.post("/register", response_model=SessionOut, status_code=status.HTTP_201_CREATED)
async def register(payload: RegisterRequest, request: Request, response: Response, db: DbSession):
    try:
        user = await auth_service.register_user(
            db, payload.email, payload.password, payload.display_name
        )
    except AuthError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc

    access_token, refresh_token = await auth_service.issue_session(
        db, user, user_agent=request.headers.get("user-agent")
    )
    _set_session_cookies(response, access_token, refresh_token)
    return SessionOut(
        access_token=access_token,
        expires_in=settings.access_token_ttl_seconds,
        user=UserOut.model_validate(user),
    )


@router.post("/login", response_model=SessionOut)
async def login(payload: LoginRequest, request: Request, response: Response, db: DbSession):
    try:
        user = await auth_service.authenticate_user(db, payload.email, payload.password)
    except AuthError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, str(exc)) from exc

    access_token, refresh_token = await auth_service.issue_session(
        db, user, user_agent=request.headers.get("user-agent")
    )
    _set_session_cookies(response, access_token, refresh_token)
    return SessionOut(
        access_token=access_token,
        expires_in=settings.access_token_ttl_seconds,
        user=UserOut.model_validate(user),
    )


@router.post("/refresh", response_model=SessionOut)
async def refresh(
    request: Request,
    response: Response,
    db: DbSession,
    refresh_token: Annotated[str | None, Cookie(alias=REFRESH_COOKIE)] = None,
):
    if not refresh_token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Missing refresh token")
    try:
        access, new_refresh, user = await auth_service.rotate_refresh_token(
            db, refresh_token, user_agent=request.headers.get("user-agent")
        )
    except AuthError as exc:
        _clear_session_cookies(response)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, str(exc)) from exc

    _set_session_cookies(response, access, new_refresh)
    return SessionOut(
        access_token=access,
        expires_in=settings.access_token_ttl_seconds,
        user=UserOut.model_validate(user),
    )


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    response: Response,
    db: DbSession,
    refresh_token: Annotated[str | None, Cookie(alias=REFRESH_COOKIE)] = None,
):
    if refresh_token:
        await auth_service.revoke_refresh_token(db, refresh_token)
    _clear_session_cookies(response)


@router.get("/me", response_model=UserOut)
async def me(user: CurrentUser):
    return UserOut.model_validate(user)
