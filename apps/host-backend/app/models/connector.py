"""MCP connector definitions, per-user connections, and encrypted credentials."""

from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class AuthKind(str, enum.Enum):
    """How a given MCP server expects us to authenticate."""

    NONE = "none"  # MCP-3: open server, no auth
    OAUTH = "oauth"  # MCP-1/4/5/google: standard OAuth 2.1 + PKCE discovery
    FORWARD = "forward"  # MCP-2: reuses this app's own auth server


class ConnectionStatus(str, enum.Enum):
    PENDING = "pending"  # created, OAuth not completed yet
    CONNECTED = "connected"
    ERROR = "error"
    EXPIRED = "expired"  # refresh failed; user must reconnect
    DISCONNECTED = "disconnected"  # tokens cleared, definition retained


class MCPConnector(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A catalog entry describing an MCP server that users may connect to.

    Seeded with the six demo servers, and extensible by the user from the
    Connectors UI.
    """

    __tablename__ = "mcp_connectors"

    slug: Mapped[str] = mapped_column(String(100), unique=True, index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    server_url: Mapped[str] = mapped_column(Text, nullable=False)
    auth_kind: Mapped[AuthKind] = mapped_column(
        Enum(AuthKind, name="auth_kind", native_enum=False, length=20),
        default=AuthKind.OAUTH,
        nullable=False,
    )
    # Optional overrides. When absent we rely on RFC 9728 + RFC 8414 discovery.
    authorization_server_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    scopes: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list, nullable=False)
    icon: Mapped[str | None] = mapped_column(String(100), nullable=True)
    # Static client credentials, needed where dynamic registration/CIMD is not
    # supported (e.g. Google, which requires a pre-registered cloud client).
    static_client_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_builtin: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    connections: Mapped[list[MCPConnection]] = relationship(
        back_populates="connector",
        cascade="all, delete-orphan",
    )


class MCPConnection(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One user's connection to one MCP server.

    `is_enabled` is deliberately independent of `status`: disabling hides the
    tools from the LLM but keeps tokens and the session intact, so re-enabling
    is instant and requires no re-authorization.
    """

    __tablename__ = "mcp_connections"
    __table_args__ = (
        UniqueConstraint("user_id", "connector_id", name="uq_mcp_connections_user_connector"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    connector_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("mcp_connectors.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    status: Mapped[ConnectionStatus] = mapped_column(
        Enum(ConnectionStatus, name="connection_status", native_enum=False, length=20),
        default=ConnectionStatus.PENDING,
        nullable=False,
    )
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    # Cached tool catalogue from the last successful list_tools call, so the
    # settings UI can render without a live round-trip.
    tools_cache: Mapped[list[dict] | None] = mapped_column(JSONB, nullable=True)
    tools_synced_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Per-tool allow list. Empty/absent means "every tool is allowed".
    enabled_tools: Mapped[list[str] | None] = mapped_column(ARRAY(Text), nullable=True)

    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_connected_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Client identity assigned to us by the remote authorization server, via
    # dynamic registration or CIMD.
    remote_client_id: Mapped[str | None] = mapped_column(Text, nullable=True)

    user: Mapped[User] = relationship(back_populates="connections")  # noqa: F821
    connector: Mapped[MCPConnector] = relationship(back_populates="connections", lazy="joined")
    credential: Mapped[MCPCredential | None] = relationship(
        back_populates="connection",
        cascade="all, delete-orphan",
        uselist=False,
    )


class MCPCredential(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """OAuth tokens for a connection, encrypted at rest with pgcrypto.

    Ciphertext lives in `bytea` columns; the plaintext never touches a normal
    column, an index, or the query log. Encryption/decryption is done with
    `pgp_sym_encrypt`/`pgp_sym_decrypt` and a key supplied per-statement from
    application settings, so the key is never stored in the database.
    """

    __tablename__ = "mcp_credentials"

    connection_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("mcp_connections.id", ondelete="CASCADE"),
        unique=True,
        index=True,
        nullable=False,
    )
    access_token_enc: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    refresh_token_enc: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    # Client secret issued by dynamic registration, if the AS returned one.
    client_secret_enc: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)

    token_type: Mapped[str] = mapped_column(String(40), default="Bearer", nullable=False)
    scopes: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list, nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    refresh_failures: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    connection: Mapped[MCPConnection] = relationship(back_populates="credential")


class OAuthTransaction(UUIDPrimaryKeyMixin, Base):
    """In-flight outbound OAuth authorization (the `state` parameter store).

    Holds the PKCE verifier and discovery results between redirecting the user
    to the MCP authorization server and receiving the callback.
    """

    __tablename__ = "oauth_transactions"

    state: Mapped[str] = mapped_column(String(128), unique=True, index=True, nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    connection_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("mcp_connections.id", ondelete="CASCADE"),
        nullable=False,
    )
    code_verifier: Mapped[str] = mapped_column(String(200), nullable=False)
    authorization_endpoint: Mapped[str] = mapped_column(Text, nullable=False)
    token_endpoint: Mapped[str] = mapped_column(Text, nullable=False)
    registration_endpoint: Mapped[str | None] = mapped_column(Text, nullable=True)
    resource: Mapped[str | None] = mapped_column(Text, nullable=True)
    client_id: Mapped[str] = mapped_column(Text, nullable=False)
    client_secret_enc: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    scopes: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list, nullable=False)
    redirect_after: Mapped[str | None] = mapped_column(Text, nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
