"""Chat endpoints, including the streaming turn."""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncGenerator

from fastapi import APIRouter, HTTPException, status
from fastapi.responses import StreamingResponse

from app.api.deps import CurrentUser, DbSession
from app.db.session import SessionLocal
from app.mcp.client_manager import MCPClientManager
from app.mcp.registry import resolve_tools
from app.models import Conversation
from app.schemas.chat import (
    ConversationDetailOut,
    ConversationOut,
    CreateConversationRequest,
    MessageOut,
    SendMessageRequest,
)
from app.services import chat_service
from app.services.chat_service import ChatError
from app.services.llm_service import LLMService, ToolCallRecord

router = APIRouter(prefix="/api/chat", tags=["chat"])


@router.get("/conversations", response_model=list[ConversationOut])
async def list_conversations(user: CurrentUser, db: DbSession):
    return await chat_service.list_conversations(db, user)


@router.post(
    "/conversations",
    response_model=ConversationOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_conversation(
    payload: CreateConversationRequest, user: CurrentUser, db: DbSession
):
    return await chat_service.create_conversation(db, user, payload.title)


@router.get("/conversations/{conversation_id}", response_model=ConversationDetailOut)
async def get_conversation(conversation_id: uuid.UUID, user: CurrentUser, db: DbSession):
    try:
        conversation = await chat_service.get_conversation(db, user, conversation_id)
    except ChatError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc

    detail = ConversationDetailOut.model_validate(conversation)
    detail.messages = [MessageOut.model_validate(m) for m in conversation.messages]
    return detail


@router.delete(
    "/conversations/{conversation_id}", status_code=status.HTTP_204_NO_CONTENT
)
async def delete_conversation(conversation_id: uuid.UUID, user: CurrentUser, db: DbSession):
    try:
        await chat_service.delete_conversation(db, user, conversation_id)
    except ChatError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc


@router.post("/conversations/{conversation_id}/messages")
async def send_message(
    conversation_id: uuid.UUID,
    payload: SendMessageRequest,
    user: CurrentUser,
    db: DbSession,
):
    """Send a message and stream the reply as Server-Sent Events.

    The streaming body runs on its own database session: the request-scoped
    session is closed by its dependency as soon as this function returns, which
    happens *before* the generator is consumed.
    """
    try:
        conversation = await chat_service.get_conversation(db, user, conversation_id)
    except ChatError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc

    await chat_service.add_user_message(db, conversation, payload.content)
    history = await chat_service.build_history(db, conversation)
    # Drop the just-added user message: it is passed separately.
    history = history[:-1] if history else []

    await db.commit()

    user_id = user.id
    conversation_id_value = conversation.id
    content = payload.content
    model = payload.model

    async def event_stream() -> AsyncGenerator[bytes, None]:
        async with SessionLocal() as session:
            try:
                tools = await resolve_tools(session, user_id)
                llm = LLMService(MCPClientManager(session))

                collected_text = ""
                tool_records: list[ToolCallRecord] = []
                input_tokens = 0
                output_tokens = 0

                async for event in llm.stream_chat(
                    history=history,
                    user_message=content,
                    tools=tools,
                    model=model,
                ):
                    etype = event.get("type")

                    if etype == "text.delta":
                        collected_text += event.get("delta", "")

                    elif etype == "tool.end":
                        tool_records.append(
                            ToolCallRecord(
                                tool_name=event.get("tool_name", ""),
                                server_label=event.get("server_label", ""),
                                arguments=event.get("arguments", {}) or {},
                                result=event.get("result"),
                                error=event.get("error"),
                                duration_ms=event.get("duration_ms", 0),
                                connection_id=_connection_id_for(
                                    tools, event.get("tool_name", "")
                                ),
                            )
                        )

                    elif etype == "done":
                        collected_text = event.get("text") or collected_text
                        input_tokens = event.get("input_tokens", 0)
                        output_tokens = event.get("output_tokens", 0)

                    yield _sse(event)

                # Persist whatever was produced, even a partial answer.
                conversation_row = await session.get(Conversation, conversation_id_value)
                if conversation_row is not None and (collected_text or tool_records):
                    await chat_service.add_assistant_message(
                        session,
                        conversation_row,
                        collected_text,
                        tool_calls=tool_records,
                        input_tokens=input_tokens,
                        output_tokens=output_tokens,
                    )
                    await session.commit()

            except Exception as exc:  # noqa: BLE001
                await session.rollback()
                yield _sse({"type": "error", "message": f"The chat turn failed: {exc}"})

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            # Stops nginx from buffering the stream into one chunk.
            "X-Accel-Buffering": "no",
        },
    )


def _connection_id_for(tools, tool_name: str):
    for tool in tools:
        if tool.descriptor.name == tool_name:
            return tool.connection.id
    return None


def _sse(payload: dict) -> bytes:
    return f"data: {json.dumps(payload)}\n\n".encode()
