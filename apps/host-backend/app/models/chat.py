"""Conversations, messages, and recorded tool calls."""

from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class MessageRole(str, enum.Enum):
    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"
    TOOL = "tool"


class ToolCallStatus(str, enum.Enum):
    PENDING = "pending"
    SUCCESS = "success"
    ERROR = "error"


class Conversation(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "conversations"

    user_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    title: Mapped[str] = mapped_column(String(300), default="New chat", nullable=False)
    model: Mapped[str | None] = mapped_column(String(100), nullable=True)
    last_message_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    messages: Mapped[list[Message]] = relationship(
        back_populates="conversation",
        cascade="all, delete-orphan",
        order_by="Message.created_at",
    )


class Message(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "messages"

    conversation_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("conversations.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    role: Mapped[MessageRole] = mapped_column(
        Enum(MessageRole, name="message_role", native_enum=False, length=20),
        nullable=False,
    )
    content: Mapped[str] = mapped_column(Text, default="", nullable=False)
    # Raw provider items for this turn, so a conversation can be replayed into
    # the Responses API without lossy reconstruction.
    raw_items: Mapped[list[dict] | None] = mapped_column(JSONB, nullable=True)
    input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    output_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)

    conversation: Mapped[Conversation] = relationship(back_populates="messages")
    tool_calls: Mapped[list[ToolCall]] = relationship(
        back_populates="message",
        cascade="all, delete-orphan",
    )


class ToolCall(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """An MCP tool invocation made while producing an assistant message."""

    __tablename__ = "tool_calls"

    message_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("messages.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    connection_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("mcp_connections.id", ondelete="SET NULL"),
        nullable=True,
    )
    tool_name: Mapped[str] = mapped_column(String(200), nullable=False)
    server_label: Mapped[str | None] = mapped_column(String(200), nullable=True)
    arguments: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    result: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    status: Mapped[ToolCallStatus] = mapped_column(
        Enum(ToolCallStatus, name="tool_call_status", native_enum=False, length=20),
        default=ToolCallStatus.PENDING,
        nullable=False,
    )
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)

    message: Mapped[Message] = relationship(back_populates="tool_calls")
