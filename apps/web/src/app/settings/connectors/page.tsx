"use client";

import { useQueryClient } from "@tanstack/react-query";
import { Loader2, Plus } from "lucide-react";
import { useSearchParams } from "next/navigation";
import { Suspense, useEffect, useState } from "react";
import { toast } from "sonner";

import { ConnectorCard } from "@/components/connector-card";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { useConnectors } from "@/hooks/use-connectors";
import { api } from "@/lib/api";

export default function ConnectorsPage() {
  return (
    <Suspense fallback={<PageSkeleton />}>
      <ConnectorsContent />
    </Suspense>
  );
}

function ConnectorsContent() {
  const { data: connectors, isLoading } = useConnectors();
  const searchParams = useSearchParams();
  const queryClient = useQueryClient();

  // The OAuth callback redirects back here with a result in the query string.
  useEffect(() => {
    const connected = searchParams.get("connected");
    const error = searchParams.get("error");
    const warning = searchParams.get("warning");

    if (connected) {
      toast.success(`Connected to ${connected}`);
      queryClient.invalidateQueries({ queryKey: ["connectors"] });
    }
    if (warning) toast.warning(warning);
    if (error) toast.error(error);

    if (connected || error || warning) {
      // Clear the params so a refresh does not replay the toast.
      window.history.replaceState({}, "", "/settings/connectors");
    }
  }, [searchParams, queryClient]);

  return (
    <div className="min-h-0 flex-1 overflow-y-auto">
      <div className="mx-auto max-w-3xl px-6 py-8">
        <div className="mb-6 flex items-end justify-between gap-4">
          <div>
            <h1 className="text-2xl font-semibold tracking-tight">Connectors</h1>
            <p className="mt-1 text-sm text-muted-foreground">
              Connect MCP servers to give the assistant new tools. Disabling a
              connector hides its tools without signing you out.
            </p>
          </div>
          <AddConnectorDialog />
        </div>

        {isLoading ? (
          <PageSkeleton />
        ) : (
          <div className="space-y-3">
            {connectors?.map((connector) => (
              <ConnectorCard key={connector.id} connector={connector} />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

function AddConnectorDialog() {
  const queryClient = useQueryClient();
  const [open, setOpen] = useState(false);
  const [name, setName] = useState("");
  const [serverUrl, setServerUrl] = useState("");
  const [authKind, setAuthKind] = useState("oauth");
  const [saving, setSaving] = useState(false);

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault();
    setSaving(true);
    try {
      await api.createConnector({
        name,
        server_url: serverUrl,
        auth_kind: authKind,
      });
      toast.success(`Added ${name}`);
      queryClient.invalidateQueries({ queryKey: ["connectors"] });
      setOpen(false);
      setName("");
      setServerUrl("");
    } catch (error) {
      toast.error((error as Error).message);
    } finally {
      setSaving(false);
    }
  }

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button variant="outline" className="gap-2">
          <Plus className="size-4" />
          Add
        </Button>
      </DialogTrigger>

      <DialogContent>
        <form onSubmit={handleSubmit}>
          <DialogHeader>
            <DialogTitle>Add an MCP server</DialogTitle>
            <DialogDescription>
              Point at any Streamable HTTP MCP endpoint. Authentication is
              discovered automatically when the server advertises it.
            </DialogDescription>
          </DialogHeader>

          <div className="space-y-4 py-4">
            <div className="space-y-2">
              <Label htmlFor="connector-name">Name</Label>
              <Input
                id="connector-name"
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="My server"
                required
              />
            </div>

            <div className="space-y-2">
              <Label htmlFor="connector-url">Server URL</Label>
              <Input
                id="connector-url"
                value={serverUrl}
                onChange={(e) => setServerUrl(e.target.value)}
                placeholder="https://example.com/mcp"
                required
              />
            </div>

            <div className="space-y-2">
              <Label htmlFor="connector-auth">Authentication</Label>
              <select
                id="connector-auth"
                value={authKind}
                onChange={(e) => setAuthKind(e.target.value)}
                className="h-9 w-full rounded-md border bg-transparent px-3 text-sm"
              >
                <option value="oauth">OAuth (discovered)</option>
                <option value="none">None</option>
                <option value="forward">Use this app&apos;s sign-in</option>
              </select>
            </div>
          </div>

          <DialogFooter>
            <Button type="submit" disabled={saving}>
              {saving ? <Loader2 className="size-4 animate-spin" /> : null}
              Add connector
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

function PageSkeleton() {
  return (
    <div className="space-y-3">
      {[0, 1, 2].map((i) => (
        <Skeleton key={i} className="h-36 w-full rounded-xl" />
      ))}
    </div>
  );
}
