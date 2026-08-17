import type {
  ChatStreamEvent,
  ConnectResponse,
  Connection,
  Connector,
  Conversation,
  ConversationDetail,
  SessionResponse,
  User,
} from "./types";

export const API_BASE =
  process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

/**
 * Fetch wrapper for the host API.
 *
 * `credentials: "include"` is essential: the session lives in an HttpOnly
 * cookie, which the browser only attaches to cross-origin requests when
 * explicitly asked.
 */
async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    ...init,
    credentials: "include",
    headers: {
      "Content-Type": "application/json",
      ...(init.headers ?? {}),
    },
  });

  if (!response.ok) {
    throw new ApiError(await extractError(response), response.status);
  }

  if (response.status === 204) {
    return undefined as T;
  }
  return (await response.json()) as T;
}

async function extractError(response: Response): Promise<string> {
  try {
    const body = await response.json();
    const detail = body?.detail ?? body?.error_description ?? body?.error;
    if (typeof detail === "string") return detail;
    if (Array.isArray(detail) && detail[0]?.msg) return detail[0].msg;
  } catch {
    // fall through to the status text
  }
  return response.statusText || `Request failed (${response.status})`;
}

export const api = {
  // ---- auth --------------------------------------------------------------
  register: (email: string, password: string, displayName?: string) =>
    request<SessionResponse>("/api/auth/register", {
      method: "POST",
      body: JSON.stringify({
        email,
        password,
        display_name: displayName || null,
      }),
    }),

  login: (email: string, password: string) =>
    request<SessionResponse>("/api/auth/login", {
      method: "POST",
      body: JSON.stringify({ email, password }),
    }),

  logout: () => request<void>("/api/auth/logout", { method: "POST" }),

  me: () => request<User>("/api/auth/me"),

  // ---- connectors --------------------------------------------------------
  listConnectors: () => request<Connector[]>("/api/connectors"),

  createConnector: (payload: {
    name: string;
    server_url: string;
    description?: string | null;
    auth_kind: string;
    scopes?: string[];
  }) =>
    request<Connector>("/api/connectors", {
      method: "POST",
      body: JSON.stringify(payload),
    }),

  connect: (connectorId: string) =>
    request<ConnectResponse>(`/api/connectors/${connectorId}/connect`, {
      method: "POST",
    }),

  setConnectionEnabled: (connectionId: string, enabled: boolean) =>
    request<Connection>(`/api/connectors/connections/${connectionId}/enabled`, {
      method: "POST",
      body: JSON.stringify({ enabled }),
    }),

  setEnabledTools: (connectionId: string, toolNames: string[] | null) =>
    request<Connection>(`/api/connectors/connections/${connectionId}/tools`, {
      method: "POST",
      body: JSON.stringify({ tool_names: toolNames }),
    }),

  refreshTools: (connectionId: string) =>
    request<Connection>(`/api/connectors/connections/${connectionId}/refresh`, {
      method: "POST",
    }),

  disconnect: (connectionId: string) =>
    request<Connection>(
      `/api/connectors/connections/${connectionId}/disconnect`,
      { method: "POST" },
    ),

  deleteConnection: (connectionId: string) =>
    request<void>(`/api/connectors/connections/${connectionId}`, {
      method: "DELETE",
    }),

  // ---- chat --------------------------------------------------------------
  listConversations: () => request<Conversation[]>("/api/chat/conversations"),

  createConversation: (title?: string) =>
    request<Conversation>("/api/chat/conversations", {
      method: "POST",
      body: JSON.stringify({ title: title ?? null }),
    }),

  getConversation: (id: string) =>
    request<ConversationDetail>(`/api/chat/conversations/${id}`),

  deleteConversation: (id: string) =>
    request<void>(`/api/chat/conversations/${id}`, { method: "DELETE" }),
};

/**
 * Send a message and consume the SSE reply.
 *
 * `fetch` is used rather than `EventSource` because the request must be a POST
 * carrying a JSON body, and must send credentials.
 */
export async function streamMessage(
  conversationId: string,
  content: string,
  onEvent: (event: ChatStreamEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  const response = await fetch(
    `${API_BASE}/api/chat/conversations/${conversationId}/messages`,
    {
      method: "POST",
      credentials: "include",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ content }),
      signal,
    },
  );

  if (!response.ok) {
    throw new ApiError(await extractError(response), response.status);
  }
  if (!response.body) {
    throw new ApiError("The server returned an empty response", 500);
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;

    buffer += decoder.decode(value, { stream: true });

    // SSE frames are separated by a blank line; the last chunk may be partial.
    const frames = buffer.split("\n\n");
    buffer = frames.pop() ?? "";

    for (const frame of frames) {
      const line = frame.split("\n").find((l) => l.startsWith("data: "));
      if (!line) continue;
      try {
        onEvent(JSON.parse(line.slice(6)) as ChatStreamEvent);
      } catch {
        // Ignore a malformed frame rather than killing the whole stream.
      }
    }
  }
}
