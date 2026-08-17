"use client";

import { ExternalLink, Plug } from "lucide-react";
import Link from "next/link";

import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Separator } from "@/components/ui/separator";
import { useCurrentUser } from "@/hooks/use-auth";
import { useConnectors } from "@/hooks/use-connectors";
import { API_BASE } from "@/lib/api";

export default function SettingsPage() {
  const { data: user } = useCurrentUser();
  const { data: connectors } = useConnectors();

  const connectedCount =
    connectors?.filter((c) => c.connection?.status === "connected").length ?? 0;

  return (
    <div className="min-h-0 flex-1 overflow-y-auto">
      <div className="mx-auto max-w-3xl space-y-6 px-6 py-8">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Settings</h1>
          <p className="mt-1 text-sm text-muted-foreground">
            Your account and this host&apos;s identity.
          </p>
        </div>

        <Card>
          <CardHeader>
            <CardTitle>Account</CardTitle>
          </CardHeader>
          <CardContent className="space-y-3 text-sm">
            <Row label="Name" value={user?.display_name ?? "—"} />
            <Separator />
            <Row label="Email" value={user?.email ?? "—"} />
            <Separator />
            <Row label="User ID" value={user?.id ?? "—"} mono />
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Connectors</CardTitle>
            <CardDescription>
              {connectedCount} of {connectors?.length ?? 0} connected.
            </CardDescription>
          </CardHeader>
          <CardContent>
            <Button asChild variant="outline" className="gap-2">
              <Link href="/settings/connectors">
                <Plug className="size-4" />
                Manage connectors
              </Link>
            </Button>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Host identity</CardTitle>
            <CardDescription>
              How this app identifies itself to MCP authorization servers. The
              client ID is a URL that resolves to a metadata document (CIMD), so
              no pre-registration is needed.
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-3 text-sm">
            <Row
              label="Client ID (CIMD)"
              value={`${API_BASE}/.well-known/oauth-client`}
              mono
              href={`${API_BASE}/.well-known/oauth-client`}
            />
            <Separator />
            <Row
              label="Redirect URI"
              value={`${API_BASE}/api/connectors/oauth/callback`}
              mono
            />
            <Separator />
            <Row
              label="Authorization server"
              value={`${API_BASE}/.well-known/oauth-authorization-server`}
              mono
              href={`${API_BASE}/.well-known/oauth-authorization-server`}
            />
          </CardContent>
        </Card>
      </div>
    </div>
  );
}

function Row({
  label,
  value,
  mono,
  href,
}: {
  label: string;
  value: string;
  mono?: boolean;
  href?: string;
}) {
  return (
    <div className="flex items-start justify-between gap-4">
      <span className="shrink-0 text-muted-foreground">{label}</span>
      <span
        className={`min-w-0 break-all text-right ${mono ? "font-mono text-xs" : ""}`}
      >
        {href ? (
          <a
            href={href}
            target="_blank"
            rel="noreferrer"
            className="inline-flex items-center gap-1 hover:underline"
          >
            {value}
            <ExternalLink className="size-3 shrink-0" />
          </a>
        ) : (
          value
        )}
      </span>
    </div>
  );
}
