export type AuthKind = "none" | "oauth" | "forward";

export type ConnectionStatus =
  | "pending"
  | "connected"
  | "error"
  | "expired"
  | "disconnected";

export interface User {
  id: string;
  email: string;
  display_name: string | null;
}

export interface SessionResponse {
  access_token: string;
  token_type: string;
  expires_in: number;
  user: User;
}

export interface ToolInfo {
  name: string;
  description: string;
  input_schema: Record<string, unknown>;
  enabled: boolean;
}

export interface Connection {
  id: string;
  status: ConnectionStatus;
  is_enabled: boolean;
  tools_synced_at: string | null;
  last_connected_at: string | null;
  last_error: string | null;
  tools: ToolInfo[];
}

export interface Connector {
  id: string;
  slug: string;
  name: string;
  description: string | null;
  server_url: string;
  auth_kind: AuthKind;
  icon: string | null;
  is_builtin: boolean;
  is_enabled: boolean;
  connection: Connection | null;
}

export interface ConnectResponse {
  connection_id: string;
  status: ConnectionStatus;
  authorization_url: string | null;
}

export type ToolCallStatus = "pending" | "success" | "error";

export interface ToolCall {
  id: string;
  tool_name: string;
  server_label: string | null;
  arguments: Record<string, unknown> | null;
  result: { text?: string } | null;
  status: ToolCallStatus;
  error: string | null;
  duration_ms: number | null;
}

export interface Message {
  id: string;
  role: "user" | "assistant" | "system" | "tool";
  content: string;
  created_at: string;
  tool_calls: ToolCall[];
}

export interface Conversation {
  id: string;
  title: string;
  created_at: string;
  last_message_at: string | null;
}

export interface ConversationDetail extends Conversation {
  messages: Message[];
}

/** Events emitted by the streaming chat endpoint. */
export type ChatStreamEvent =
  | { type: "text.delta"; delta: string }
  | {
      type: "tool.start";
      tool_name: string;
      server_label: string;
      arguments: Record<string, unknown>;
    }
  | {
      type: "tool.end";
      tool_name: string;
      server_label: string;
      ok: boolean;
      result: string | null;
      error: string | null;
      duration_ms: number;
    }
  | { type: "done"; text: string; input_tokens: number; output_tokens: number }
  | { type: "error"; message: string };

/** A tool invocation being rendered live, before it is persisted. */
export interface LiveToolCall {
  tool_name: string;
  server_label: string;
  arguments: Record<string, unknown>;
  status: "running" | "success" | "error";
  result?: string | null;
  error?: string | null;
  duration_ms?: number;
}
