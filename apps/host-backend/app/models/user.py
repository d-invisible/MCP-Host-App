"""User + this app's own OAuth server records."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, String, Text
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class User(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "users"

    email: Mapped[str] = mapped_column(String(320), unique=True, index=True, nullable=False)
    # Null for users created purely through an external IdP.
    password_hash: Mapped[str | None] = mapped_column(Text, nullable=True)
    display_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    connections: Mapped[list[MCPConnection]] = relationship(  # noqa: F821
        back_populates="user",
        cascade="all, delete-orphan",
        lazy="selectin",
    )


class RefreshToken(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Refresh tokens for this app's own sessions.

    Only the SHA-256 hash is stored, so a database leak does not yield usable
    tokens. Rotation is tracked via `replaced_by_id` to detect token reuse.
    """

    __tablename__ = "refresh_tokens"

    user_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    replaced_by_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), nullable=True
    )
    user_agent: Mapped[str | None] = mapped_column(String(400), nullable=True)


class OAuthClient(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A client registered against *our* authorization server.

    Populated two ways:
      * dynamic client registration (RFC 7591), used by MCP-2 style servers
      * CIMD, where `client_id` is an https URL resolving to a metadata document
    """

    __tablename__ = "oauth_clients"

    client_id: Mapped[str] = mapped_column(String(500), unique=True, index=True, nullable=False)
    # Null for public clients using PKCE.
    client_secret_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    client_name: Mapped[str] = mapped_column(String(200), nullable=False)
    redirect_uris: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False)
    scopes: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list, nullable=False)
    grant_types: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list, nullable=False)
    is_cimd: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_trusted: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class AuthorizationCode(UUIDPrimaryKeyMixin, Base):
    """Short-lived authorization codes issued by our auth server (PKCE required)."""

    __tablename__ = "authorization_codes"
    __table_args__ = (Index("ix_authorization_codes_expires_at", "expires_at"),)

    code_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    client_id: Mapped[str] = mapped_column(String(500), nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    redirect_uri: Mapped[str] = mapped_column(Text, nullable=False)
    scopes: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list, nullable=False)
    code_challenge: Mapped[str] = mapped_column(String(200), nullable=False)
    code_challenge_method: Mapped[str] = mapped_column(String(10), default="S256", nullable=False)
    # RFC 8707 resource indicator -> becomes the `aud` of the issued token.
    resource: Mapped[str | None] = mapped_column(Text, nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
