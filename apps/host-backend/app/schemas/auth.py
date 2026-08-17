"""Request/response models for authentication endpoints."""

from __future__ import annotations

import uuid

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=200)
    display_name: str | None = Field(default=None, max_length=200)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=200)


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: str
    display_name: str | None = None


class SessionOut(BaseModel):
    """Returned on login/register/refresh.

    The access token is also set as an HttpOnly cookie; it is echoed here for
    non-browser API clients.
    """

    access_token: str
    token_type: str = "Bearer"
    expires_in: int
    user: UserOut
