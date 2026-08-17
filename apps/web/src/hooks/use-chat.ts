"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useCallback, useRef, useState } from "react";
import { toast } from "sonner";

import { api, streamMessage } from "@/lib/api";
import type {
  Conversation,
  ConversationDetail,
  LiveToolCall,
} from "@/lib/types";

export function useConversations() {
  return useQuery<Conversation[]>({
    queryKey: ["conversations"],
    queryFn: api.listConversations,
  });
}

export function useConversation(id: string | null) {
  return useQuery<ConversationDetail>({
    queryKey: ["conversation", id],
    queryFn: () => api.getConversation(id!),
    enabled: Boolean(id),
  });
}

export function useCreateConversation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (title?: string) => api.createConversation(title),
    onSuccess: () =>
      queryClient.invalidateQueries({ queryKey: ["conversations"] }),
  });
}

export function useDeleteConversation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api.deleteConversation(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["conversations"] });
      toast.success("Conversation deleted");
    },
  });
}

/**
 * Drives one streaming turn.
 *
 * Streaming state is kept in local component state rather than the query
 * cache, because it changes on nearly every frame; the cache is refreshed once
 * at the end so the persisted messages become the source of truth.
 */
export function useChatStream(conversationId: string | null) {
  const queryClient = useQueryClient();
  const [streamingText, setStreamingText] = useState("");
  const [liveToolCalls, setLiveToolCalls] = useState<LiveToolCall[]>([]);
  const [isStreaming, setIsStreaming] = useState(false);
  const abortRef = useRef<AbortController | null>(null);

  const send = useCallback(
    async (content: string) => {
      if (!conversationId || isStreaming) return;

      setIsStreaming(true);
      setStreamingText("");
      setLiveToolCalls([]);

      const controller = new AbortController();
      abortRef.current = controller;

      try {
        await streamMessage(
          conversationId,
          content,
          (event) => {
            switch (event.type) {
              case "text.delta":
                setStreamingText((prev) => prev + event.delta);
                break;

              case "tool.start":
                setLiveToolCalls((prev) => [
                  ...prev,
                  {
                    tool_name: event.tool_name,
                    server_label: event.server_label,
                    arguments: event.arguments,
                    status: "running",
                  },
                ]);
                break;

              case "tool.end":
                setLiveToolCalls((prev) => {
                  const next = [...prev];
                  // Update the most recent running entry for this tool.
                  for (let i = next.length - 1; i >= 0; i--) {
                    if (
                      next[i].tool_name === event.tool_name &&
                      next[i].status === "running"
                    ) {
                      next[i] = {
                        ...next[i],
                        status: event.ok ? "success" : "error",
                        result: event.result,
                        error: event.error,
                        duration_ms: event.duration_ms,
                      };
                      break;
                    }
                  }
                  return next;
                });
                break;

              case "error":
                toast.error(event.message);
                break;

              case "done":
                break;
            }
          },
          controller.signal,
        );
      } catch (error) {
        if ((error as Error).name !== "AbortError") {
          toast.error((error as Error).message);
        }
      } finally {
        setIsStreaming(false);
        abortRef.current = null;
        // Pull the persisted turn, then clear the local streaming buffer so
        // the message is not rendered twice.
        await queryClient.invalidateQueries({
          queryKey: ["conversation", conversationId],
        });
        await queryClient.invalidateQueries({ queryKey: ["conversations"] });
        setStreamingText("");
        setLiveToolCalls([]);
      }
    },
    [conversationId, isStreaming, queryClient],
  );

  const stop = useCallback(() => abortRef.current?.abort(), []);

  return { send, stop, streamingText, liveToolCalls, isStreaming };
}
