"use client";

import {
  ArrowUp,
  Loader2,
  MessageSquarePlus,
  Plug,
  Square,
  Trash2,
} from "lucide-react";
import Link from "next/link";
import { useEffect, useMemo, useRef, useState } from "react";

import { ToolCallCard } from "@/components/tool-call-card";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import {
  useChatStream,
  useConversation,
  useConversations,
  useCreateConversation,
  useDeleteConversation,
} from "@/hooks/use-chat";
import { useConnectors } from "@/hooks/use-connectors";
import { cn } from "@/lib/utils";

export default function ChatPage() {
  const [activeId, setActiveId] = useState<string | null>(null);
  const [input, setInput] = useState("");

  const { data: conversations } = useConversations();
  const { data: conversation } = useConversation(activeId);
  const { data: connectors } = useConnectors();
  const createConversation = useCreateConversation();
  const deleteConversation = useDeleteConversation();

  const { send, stop, streamingText, liveToolCalls, isStreaming } =
    useChatStream(activeId);

  const scrollRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  // Open the most recent conversation on first load.
  useEffect(() => {
    if (!activeId && conversations?.length) setActiveId(conversations[0].id);
  }, [conversations, activeId]);

  useEffect(() => {
    scrollRef.current?.scrollTo({
      top: scrollRef.current.scrollHeight,
      behavior: "smooth",
    });
  }, [conversation?.messages, streamingText, liveToolCalls]);

  const activeToolCount = useMemo(
    () =>
      connectors
        ?.filter((c) => c.connection?.is_enabled && c.connection.status === "connected")
        .reduce(
          (total, c) =>
            total + (c.connection?.tools.filter((t) => t.enabled).length ?? 0),
          0,
        ) ?? 0,
    [connectors],
  );

  async function handleSend() {
    const content = input.trim();
    if (!content || isStreaming) return;

    let conversationId = activeId;
    if (!conversationId) {
      const created = await createConversation.mutateAsync(undefined);
      conversationId = created.id;
      setActiveId(created.id);
    }

    setInput("");
    await send(content);
  }

  const messages = conversation?.messages ?? [];
  const showEmptyState = messages.length === 0 && !streamingText && !isStreaming;

  return (
    <div className="flex h-full min-h-0">
      {/* conversation list */}
      <aside className="flex w-64 shrink-0 flex-col border-r bg-muted/20">
        <div className="p-3">
          <Button
            className="w-full justify-start gap-2"
            variant="outline"
            onClick={async () => {
              const created = await createConversation.mutateAsync(undefined);
              setActiveId(created.id);
            }}
          >
            <MessageSquarePlus className="size-4" />
            New chat
          </Button>
        </div>

        <div className="min-h-0 flex-1 overflow-y-auto px-2 pb-2">
          {conversations?.map((item) => (
            <div
              key={item.id}
              className={cn(
                "group flex items-center gap-1 rounded-md px-2 py-2 text-sm",
                activeId === item.id ? "bg-accent" : "hover:bg-accent/50",
              )}
            >
              <button
                type="button"
                onClick={() => setActiveId(item.id)}
                className="min-w-0 flex-1 truncate text-left"
                title={item.title}
              >
                {item.title}
              </button>
              <Button
                variant="ghost"
                size="icon"
                aria-label="Delete conversation"
                className="size-7 opacity-0 group-hover:opacity-100"
                onClick={async () => {
                  await deleteConversation.mutateAsync(item.id);
                  if (activeId === item.id) setActiveId(null);
                }}
              >
                <Trash2 className="size-3.5" />
              </Button>
            </div>
          ))}
        </div>

        <div className="border-t p-3 text-xs text-muted-foreground">
          <Link
            href="/settings/connectors"
            className="flex items-center gap-2 hover:text-foreground"
          >
            <Plug className="size-3.5" />
            {activeToolCount} tool{activeToolCount === 1 ? "" : "s"} available
          </Link>
        </div>
      </aside>

      {/* thread */}
      <div className="flex min-w-0 flex-1 flex-col">
        <div ref={scrollRef} className="min-h-0 flex-1 overflow-y-auto">
          <div className="mx-auto max-w-3xl px-4 py-6">
            {showEmptyState ? (
              <EmptyState toolCount={activeToolCount} />
            ) : (
              <div className="space-y-6">
                {messages.map((message) => (
                  <div key={message.id}>
                    {message.tool_calls.length > 0 ? (
                      <div className="mb-2">
                        {message.tool_calls.map((call) => (
                          <ToolCallCard
                            key={call.id}
                            toolName={call.tool_name}
                            serverLabel={call.server_label}
                            args={call.arguments}
                            result={call.result?.text ?? null}
                            error={call.error}
                            status={call.status === "error" ? "error" : "success"}
                            durationMs={call.duration_ms}
                          />
                        ))}
                      </div>
                    ) : null}
                    <MessageBubble role={message.role} content={message.content} />
                  </div>
                ))}

                {liveToolCalls.map((call, index) => (
                  <ToolCallCard
                    key={`live-${index}`}
                    toolName={call.tool_name}
                    serverLabel={call.server_label}
                    args={call.arguments}
                    result={call.result}
                    error={call.error}
                    status={call.status}
                    durationMs={call.duration_ms}
                  />
                ))}

                {streamingText ? (
                  <MessageBubble role="assistant" content={streamingText} />
                ) : null}

                {isStreaming && !streamingText && liveToolCalls.length === 0 ? (
                  <div className="flex items-center gap-2 text-sm text-muted-foreground">
                    <Loader2 className="size-4 animate-spin" />
                    Thinking…
                  </div>
                ) : null}
              </div>
            )}
          </div>
        </div>

        {/* composer */}
        <div className="border-t bg-background p-4">
          <div className="mx-auto flex max-w-3xl items-end gap-2">
            <Textarea
              ref={textareaRef}
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => {
                // Enter sends; Shift+Enter inserts a newline.
                if (e.key === "Enter" && !e.shiftKey) {
                  e.preventDefault();
                  void handleSend();
                }
              }}
              placeholder="Ask anything, or use a connected tool…"
              className="max-h-48 min-h-[52px] resize-none"
              disabled={isStreaming}
            />
            {isStreaming ? (
              <Button size="icon" variant="outline" onClick={stop} title="Stop">
                <Square className="size-4" />
              </Button>
            ) : (
              <Button
                size="icon"
                onClick={handleSend}
                disabled={!input.trim()}
                title="Send"
              >
                <ArrowUp className="size-4" />
              </Button>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

function MessageBubble({
  role,
  content,
}: {
  role: string;
  content: string;
}) {
  if (role === "user") {
    return (
      <div className="flex justify-end">
        <div className="max-w-[85%] whitespace-pre-wrap break-words rounded-2xl bg-primary px-4 py-2.5 text-primary-foreground">
          {content}
        </div>
      </div>
    );
  }

  return (
    <div className="whitespace-pre-wrap break-words leading-relaxed">
      {content}
    </div>
  );
}

function EmptyState({ toolCount }: { toolCount: number }) {
  return (
    <div className="flex flex-col items-center justify-center py-24 text-center">
      <h2 className="text-xl font-semibold">How can I help?</h2>
      <p className="mt-2 max-w-sm text-sm text-muted-foreground">
        {toolCount > 0
          ? `You have ${toolCount} tool${toolCount === 1 ? "" : "s"} connected. Ask something that uses them.`
          : "No tools are connected yet. Add a connector to give the assistant new abilities."}
      </p>
      {toolCount === 0 ? (
        <Button asChild variant="outline" className="mt-4 gap-2">
          <Link href="/settings/connectors">
            <Plug className="size-4" />
            Browse connectors
          </Link>
        </Button>
      ) : null}
    </div>
  );
}
