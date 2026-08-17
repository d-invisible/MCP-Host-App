"""Request/response models for chat endpoints."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models import MessageRole, ToolCallStatus


class ToolCallOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    tool_name: str
    server_label: str | None = None
    arguments: dict | None = None
    result: dict | None = None
    status: ToolCallStatus
    error: str | None = None
    duration_ms: int | None = None


class MessageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    role: MessageRole
    content: str
    created_at: datetime
    tool_calls: list[ToolCallOut] = Field(default_factory=list)


class ConversationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    title: str
    created_at: datetime
    last_message_at: datetime | None = None


class ConversationDetailOut(ConversationOut):
    messages: list[MessageOut] = Field(default_factory=list)


class CreateConversationRequest(BaseModel):
    title: str | None = Field(default=None, max_length=300)


class SendMessageRequest(BaseModel):
    content: str = Field(min_length=1, max_length=32000)
    model: str | None = None
