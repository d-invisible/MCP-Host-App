"""Conversation persistence and history rebuilding."""

from __future__ import annotations

import uuid

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models import Conversation, Message, MessageRole, ToolCall, ToolCallStatus, User

# How many past messages to replay into the model. Older turns are dropped
# rather than summarized; summarization is a later concern.
HISTORY_LIMIT = 40


class ChatError(Exception):
    """Raised for chat problems the caller should surface to the user."""


async def list_conversations(db: AsyncSession, user: User) -> list[Conversation]:
    return list(
        await db.scalars(
            select(Conversation)
            .where(Conversation.user_id == user.id)
            .order_by(desc(Conversation.last_message_at), desc(Conversation.created_at))
        )
    )


async def create_conversation(
    db: AsyncSession, user: User, title: str | None = None
) -> Conversation:
    conversation = Conversation(user_id=user.id, title=title or "New chat")
    db.add(conversation)
    await db.flush()
    return conversation


async def get_conversation(
    db: AsyncSession, user: User, conversation_id: uuid.UUID
) -> Conversation:
    conversation = await db.scalar(
        select(Conversation)
        .where(Conversation.id == conversation_id)
        .options(
            selectinload(Conversation.messages).selectinload(Message.tool_calls)
        )
    )
    if conversation is None or conversation.user_id != user.id:
        raise ChatError("Conversation not found")
    return conversation


async def delete_conversation(db: AsyncSession, user: User, conversation_id: uuid.UUID) -> None:
    conversation = await get_conversation(db, user, conversation_id)
    await db.delete(conversation)
    await db.flush()


async def build_history(db: AsyncSession, conversation: Conversation) -> list[dict]:
    """Rebuild Responses API input items from stored messages.

    Only user and assistant text is replayed. Tool calls are intentionally not
    replayed: their `call_id`s belong to earlier responses, and the tools
    available now may differ from those available then.
    """
    messages = list(
        await db.scalars(
            select(Message)
            .where(Message.conversation_id == conversation.id)
            .order_by(Message.created_at)
        )
    )

    history: list[dict] = []
    for message in messages[-HISTORY_LIMIT:]:
        if message.role is MessageRole.USER:
            history.append({"role": "user", "content": message.content})
        elif message.role is MessageRole.ASSISTANT and message.content:
            history.append({"role": "assistant", "content": message.content})
    return history


async def add_user_message(
    db: AsyncSession, conversation: Conversation, content: str
) -> Message:
    message = Message(
        conversation_id=conversation.id,
        role=MessageRole.USER,
        content=content,
    )
    db.add(message)

    # Name the conversation after its first user message.
    if conversation.title == "New chat":
        conversation.title = content.strip()[:80] or "New chat"

    from datetime import UTC, datetime

    conversation.last_message_at = datetime.now(UTC)
    await db.flush()
    return message


async def add_assistant_message(
    db: AsyncSession,
    conversation: Conversation,
    content: str,
    *,
    tool_calls: list | None = None,
    input_tokens: int = 0,
    output_tokens: int = 0,
) -> Message:
    message = Message(
        conversation_id=conversation.id,
        role=MessageRole.ASSISTANT,
        content=content,
        input_tokens=input_tokens or None,
        output_tokens=output_tokens or None,
    )
    db.add(message)
    await db.flush()

    for record in tool_calls or []:
        db.add(
            ToolCall(
                message_id=message.id,
                connection_id=record.connection_id,
                tool_name=record.tool_name,
                server_label=record.server_label,
                arguments=record.arguments,
                result={"text": record.result} if record.result is not None else None,
                status=ToolCallStatus.ERROR if record.error else ToolCallStatus.SUCCESS,
                error=record.error,
                duration_ms=record.duration_ms,
            )
        )

    from datetime import UTC, datetime

    conversation.last_message_at = datetime.now(UTC)
    await db.flush()
    return message
