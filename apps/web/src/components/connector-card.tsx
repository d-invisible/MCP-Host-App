"use client";

import {
  AlertCircle,
  Link2Off,
  Loader2,
  Lock,
  RefreshCw,
  ShieldCheck,
  Trash2,
  Unlock,
} from "lucide-react";
import { useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Switch } from "@/components/ui/switch";
import {
  useConnect,
  useDeleteConnection,
  useDisconnect,
  useRefreshTools,
  useSetConnectionEnabled,
  useSetEnabledTools,
} from "@/hooks/use-connectors";
import type { Connector } from "@/lib/types";
import { cn } from "@/lib/utils";

const AUTH_LABEL: Record<string, { label: string; icon: typeof Lock }> = {
  none: { label: "No sign-in", icon: Unlock },
  oauth: { label: "OAuth", icon: Lock },
  forward: { label: "Uses this app's login", icon: ShieldCheck },
};

export function ConnectorCard({ connector }: { connector: Connector }) {
  const [expanded, setExpanded] = useState(false);

  const connect = useConnect();
  const setEnabled = useSetConnectionEnabled();
  const setEnabledTools = useSetEnabledTools();
  const refreshTools = useRefreshTools();
  const disconnect = useDisconnect();
  const deleteConnection = useDeleteConnection();

  const connection = connector.connection;
  const isConnected = connection?.status === "connected";
  const needsAttention =
    connection?.status === "error" || connection?.status === "expired";

  const auth = AUTH_LABEL[connector.auth_kind] ?? AUTH_LABEL.oauth;
  const AuthIcon = auth.icon;

  const enabledToolCount =
    connection?.tools.filter((tool) => tool.enabled).length ?? 0;

  function toggleTool(toolName: string, enabled: boolean) {
    if (!connection) return;

    // The API stores an allow list. An empty list means "all allowed", so it
    // has to be materialised the first time a single tool is switched off.
    const current = connection.tools.filter((t) => t.enabled).map((t) => t.name);
    const next = enabled
      ? [...current, toolName]
      : current.filter((name) => name !== toolName);

    setEnabledTools.mutate({
      connectionId: connection.id,
      toolNames: next.length === connection.tools.length ? null : next,
    });
  }

  return (
    <Card className={cn(needsAttention && "border-destructive/50")}>
      <CardContent className="space-y-4 p-5">
        <div className="flex items-start gap-4">
          <div className="min-w-0 flex-1">
            <div className="flex flex-wrap items-center gap-2">
              <h3 className="font-medium">{connector.name}</h3>

              <Badge variant="secondary" className="gap-1 text-xs font-normal">
                <AuthIcon className="size-3" />
                {auth.label}
              </Badge>

              {isConnected ? (
                <Badge className="bg-emerald-600 text-xs hover:bg-emerald-600">
                  Connected
                </Badge>
              ) : null}
              {needsAttention ? (
                <Badge variant="destructive" className="gap-1 text-xs">
                  <AlertCircle className="size-3" />
                  {connection?.status === "expired"
                    ? "Sign-in expired"
                    : "Error"}
                </Badge>
              ) : null}
            </div>

            {connector.description ? (
              <p className="mt-1 text-sm text-muted-foreground">
                {connector.description}
              </p>
            ) : null}
            <p className="mt-1 truncate font-mono text-xs text-muted-foreground">
              {connector.server_url}
            </p>
          </div>

          {/* enable/disable keeps credentials; only shown once connected */}
          {isConnected || connection?.status === "disconnected" ? null : null}

          {isConnected ? (
            <div className="flex shrink-0 items-center gap-2">
              <span className="text-xs text-muted-foreground">
                {connection?.is_enabled ? "Enabled" : "Disabled"}
              </span>
              <Switch
                checked={connection?.is_enabled ?? false}
                aria-label="Enable connector"
                onCheckedChange={(checked) =>
                  setEnabled.mutate({
                    connectionId: connection!.id,
                    enabled: checked,
                  })
                }
              />
            </div>
          ) : null}
        </div>

        {connection?.last_error ? (
          <p className="rounded-md bg-destructive/10 px-3 py-2 text-sm text-destructive">
            {connection.last_error}
          </p>
        ) : null}

        <div className="flex flex-wrap items-center gap-2">
          {!isConnected ? (
            <Button
              size="sm"
              disabled={connect.isPending}
              onClick={() => connect.mutate(connector.id)}
            >
              {connect.isPending ? (
                <Loader2 className="size-4 animate-spin" />
              ) : null}
              {connection?.status === "expired" ? "Reconnect" : "Connect"}
            </Button>
          ) : (
            <>
              <Button
                size="sm"
                variant="outline"
                onClick={() => setExpanded((v) => !v)}
              >
                {expanded ? "Hide" : "Show"} tools ({enabledToolCount}/
                {connection?.tools.length ?? 0})
              </Button>

              <Button
                size="sm"
                variant="ghost"
                disabled={refreshTools.isPending}
                onClick={() => refreshTools.mutate(connection!.id)}
              >
                <RefreshCw
                  className={cn(
                    "size-4",
                    refreshTools.isPending && "animate-spin",
                  )}
                />
                Refresh
              </Button>

              <Button
                size="sm"
                variant="ghost"
                className="text-muted-foreground"
                onClick={() => disconnect.mutate(connection!.id)}
              >
                <Link2Off className="size-4" />
                Disconnect
              </Button>
            </>
          )}

          {connection ? (
            <Button
              size="sm"
              variant="ghost"
              className="ml-auto text-destructive hover:text-destructive"
              onClick={() => deleteConnection.mutate(connection.id)}
            >
              <Trash2 className="size-4" />
              Delete
            </Button>
          ) : null}
        </div>

        {expanded && connection?.tools.length ? (
          <div className="space-y-1 rounded-lg border bg-muted/30 p-3">
            {connection.tools.map((tool) => (
              <div
                key={tool.name}
                className="flex items-start gap-3 rounded-md px-2 py-1.5 hover:bg-background/60"
              >
                <div className="min-w-0 flex-1">
                  <p className="font-mono text-sm">{tool.name}</p>
                  {tool.description ? (
                    <p className="text-xs text-muted-foreground">
                      {tool.description}
                    </p>
                  ) : null}
                </div>
                <Switch
                  checked={tool.enabled}
                  aria-label={`Enable ${tool.name}`}
                  disabled={!connection.is_enabled}
                  onCheckedChange={(checked) => toggleTool(tool.name, checked)}
                />
              </div>
            ))}
          </div>
        ) : null}
      </CardContent>
    </Card>
  );
}
