"""LLM orchestration over the OpenAI Responses API, with MCP tool calling.

Why the tool loop is implemented here rather than handed to the Agents SDK's
MCP client: that client takes a static `Authorization` header and owns its own
connection, so it cannot refresh an OAuth token that expires mid-run, and it
cannot enforce our per-user enable/disable rules. Running the loop ourselves
means every tool call goes through `MCPClientManager`, which refreshes tokens
first and routes to exactly the connections the user has enabled.

Works against api.openai.com and Azure AI Foundry; only the base URL and the
optional api-version differ.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import AsyncGenerator
from dataclasses import dataclass, field
from typing import Any

from openai import AsyncOpenAI

from app.core.config import settings
from app.mcp.client_manager import MCPClientManager, MCPConnectionError
from app.mcp.registry import ResolvedTool, index_by_qualified_name, to_openai_tools

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are the assistant inside an MCP host application.

You may have tools available from MCP servers the user has connected. Each tool
name is prefixed with the connector it belongs to.

Guidelines:
- Use a tool when it genuinely helps; answer directly when it does not.
- Never invent tool results. If a tool fails, say so plainly and explain what
  the user could do about it.
- If the user asks for something that would need a connector they have not
  connected or enabled, tell them which one it is.
- Be concise and concrete."""


@dataclass(slots=True)
class ToolCallRecord:
    """One tool invocation, captured for persistence and for the UI."""

    tool_name: str
    server_label: str
    arguments: dict[str, Any]
    result: str | None = None
    error: str | None = None
    duration_ms: int = 0
    connection_id: Any = None


@dataclass(slots=True)
class ChatResult:
    text: str
    tool_calls: list[ToolCallRecord] = field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0
    raw_items: list[dict] = field(default_factory=list)


def build_client() -> AsyncOpenAI:
    """Construct the LLM client.

    Azure AI Foundry's `/openai/v1` surface is OpenAI-compatible, so the only
    differences are the base URL, the key, and using the *deployment* name in
    place of a model id. Foundry accepts the key as a bearer token on that
    surface, so no separate auth path is needed.
    """
    kwargs: dict[str, Any] = {
        "api_key": settings.llm_api_key or "missing-api-key",
        "timeout": settings.llm_request_timeout_seconds,
    }
    if settings.llm_base_url:
        kwargs["base_url"] = settings.llm_base_url
    if settings.openai_api_version:
        kwargs["default_query"] = {"api-version": settings.openai_api_version}
    return AsyncOpenAI(**kwargs)


class LLMService:
    def __init__(self, mcp_manager: MCPClientManager) -> None:
        self._client = build_client()
        self._mcp = mcp_manager

    async def stream_chat(
        self,
        *,
        history: list[dict],
        user_message: str,
        tools: list[ResolvedTool],
        model: str | None = None,
    ) -> AsyncGenerator[dict, None]:
        """Run a chat turn, yielding SSE-shaped events.

        Event types: `text.delta`, `tool.start`, `tool.end`, `done`, `error`.
        """
        model = model or settings.llm_model
        tool_index = index_by_qualified_name(tools)
        tool_defs = to_openai_tools(tools)

        conversation: list[dict] = [
            *history,
            {"role": "user", "content": user_message},
        ]

        result = ChatResult(text="")

        for _ in range(settings.llm_max_tool_iterations):
            try:
                stream = await self._client.responses.create(
                    model=model,
                    instructions=SYSTEM_PROMPT,
                    input=conversation,
                    tools=tool_defs or None,
                    stream=True,
                )
            except Exception as exc:  # noqa: BLE001
                logger.exception("LLM request failed")
                yield {"type": "error", "message": _friendly_llm_error(exc)}
                return

            turn_text = ""
            function_calls: list[dict] = []
            output_items: list[Any] = []

            async for event in stream:
                etype = getattr(event, "type", "")

                if etype == "response.output_text.delta":
                    delta = getattr(event, "delta", "") or ""
                    turn_text += delta
                    yield {"type": "text.delta", "delta": delta}

                elif etype == "response.completed":
                    response = getattr(event, "response", None)
                    if response is None:
                        continue
                    output_items = list(getattr(response, "output", []) or [])
                    usage = getattr(response, "usage", None)
                    if usage is not None:
                        result.input_tokens += getattr(usage, "input_tokens", 0) or 0
                        result.output_tokens += getattr(usage, "output_tokens", 0) or 0

                    for item in output_items:
                        if getattr(item, "type", "") == "function_call":
                            function_calls.append(
                                {
                                    "call_id": getattr(item, "call_id", ""),
                                    "name": getattr(item, "name", ""),
                                    "arguments": getattr(item, "arguments", "") or "{}",
                                }
                            )

                elif etype == "error":
                    message = getattr(event, "message", "The model returned an error")
                    yield {"type": "error", "message": message}
                    return

            result.text = turn_text

            # No tool calls: the turn is complete.
            if not function_calls:
                yield {
                    "type": "done",
                    "text": result.text,
                    "input_tokens": result.input_tokens,
                    "output_tokens": result.output_tokens,
                }
                return

            # Carry the model's own output forward, then append tool results.
            conversation.extend(_serialize_items(output_items))

            for call in function_calls:
                record, output_payload = await self._execute_tool(call, tool_index)
                result.tool_calls.append(record)

                yield {
                    "type": "tool.start",
                    "tool_name": record.tool_name,
                    "server_label": record.server_label,
                    "arguments": record.arguments,
                }
                yield {
                    "type": "tool.end",
                    "tool_name": record.tool_name,
                    "server_label": record.server_label,
                    "ok": record.error is None,
                    "result": record.result,
                    "error": record.error,
                    "duration_ms": record.duration_ms,
                }

                conversation.append(
                    {
                        "type": "function_call_output",
                        "call_id": call["call_id"],
                        "output": output_payload,
                    }
                )

        # Exhausted the iteration budget without a final answer.
        yield {
            "type": "error",
            "message": (
                "Stopped after too many tool calls without reaching an answer. "
                "Try narrowing the request."
            ),
        }

    async def _execute_tool(
        self,
        call: dict,
        tool_index: dict[str, ResolvedTool],
    ) -> tuple[ToolCallRecord, str]:
        """Route one tool call to its MCP connection."""
        qualified = call["name"]
        started = time.perf_counter()

        try:
            arguments = json.loads(call["arguments"] or "{}")
        except json.JSONDecodeError:
            arguments = {}

        resolved = tool_index.get(qualified)
        if resolved is None:
            # The model asked for a tool that is no longer available — most
            # likely the user disabled the connector mid-conversation.
            record = ToolCallRecord(
                tool_name=qualified,
                server_label="unknown",
                arguments=arguments,
                error="This tool is not currently available.",
                duration_ms=0,
            )
            return record, "Error: this tool is not currently available to you."

        record = ToolCallRecord(
            tool_name=resolved.descriptor.name,
            server_label=resolved.descriptor.connector_name,
            arguments=arguments,
            connection_id=resolved.connection.id,
        )

        try:
            text, is_error = await self._mcp.call_tool(
                resolved.connection, resolved.descriptor.name, arguments
            )
            record.duration_ms = int((time.perf_counter() - started) * 1000)

            if is_error:
                record.error = text or "The tool reported an error"
                return record, f"Error: {record.error}"

            record.result = text
            return record, text or "(the tool returned no content)"

        except MCPConnectionError as exc:
            record.duration_ms = int((time.perf_counter() - started) * 1000)
            record.error = str(exc)
            return record, f"Error: {exc}"

        except Exception as exc:  # noqa: BLE001
            logger.exception("Tool execution failed for %s", qualified)
            record.duration_ms = int((time.perf_counter() - started) * 1000)
            record.error = str(exc)
            return record, f"Error calling this tool: {exc}"


def _serialize_items(items: list[Any]) -> list[dict]:
    """Convert SDK output objects into plain dicts for the next request."""
    serialized: list[dict] = []
    for item in items:
        if hasattr(item, "model_dump"):
            serialized.append(item.model_dump(exclude_none=True))
        elif isinstance(item, dict):
            serialized.append(item)
    return serialized


def _friendly_llm_error(exc: Exception) -> str:
    text = str(exc)
    key_var = (
        "AZURE_AI_FOUNDRY_API_KEY" if settings.is_azure_foundry else "OPENAI_API_KEY"
    )

    if "api_key" in text.lower() or "401" in text:
        return f"The AI provider rejected the API key. Check {key_var} in your .env."
    if "429" in text:
        return "The AI provider is rate limiting this request. Try again shortly."
    if "404" in text or ("model" in text.lower() and "not found" in text.lower()):
        if settings.is_azure_foundry:
            return (
                f"Deployment {settings.llm_model!r} was not found at this Foundry "
                "endpoint. AZURE_AI_FOUNDRY_DEPLOYMENT must be the deployment name, "
                "not the model name."
            )
        return f"Model {settings.llm_model!r} is unavailable to this key or endpoint."
    return f"The AI request failed: {text}"
