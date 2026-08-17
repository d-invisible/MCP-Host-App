"use client";

import {
  CheckCircle2,
  ChevronRight,
  Loader2,
  XCircle,
  Wrench,
} from "lucide-react";
import { useState } from "react";

import { cn } from "@/lib/utils";

interface ToolCallCardProps {
  toolName: string;
  serverLabel: string | null;
  args: Record<string, unknown> | null;
  result?: string | null;
  error?: string | null;
  status: "running" | "success" | "error";
  durationMs?: number | null;
}

/** Collapsed-by-default record of one tool invocation. */
export function ToolCallCard({
  toolName,
  serverLabel,
  args,
  result,
  error,
  status,
  durationMs,
}: ToolCallCardProps) {
  const [open, setOpen] = useState(false);

  return (
    <div className="my-2 overflow-hidden rounded-lg border bg-muted/40 text-sm">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center gap-2 px-3 py-2 text-left hover:bg-muted/60"
      >
        <ChevronRight
          className={cn(
            "size-4 shrink-0 text-muted-foreground transition-transform",
            open && "rotate-90",
          )}
        />
        <Wrench className="size-4 shrink-0 text-muted-foreground" />

        <span className="truncate font-medium">{toolName}</span>
        {serverLabel ? (
          <span className="shrink-0 rounded bg-background px-1.5 py-0.5 text-xs text-muted-foreground">
            {serverLabel}
          </span>
        ) : null}

        <span className="ml-auto flex shrink-0 items-center gap-2">
          {durationMs ? (
            <span className="text-xs text-muted-foreground">{durationMs}ms</span>
          ) : null}
          {status === "running" ? (
            <Loader2 className="size-4 animate-spin text-muted-foreground" />
          ) : status === "success" ? (
            <CheckCircle2 className="size-4 text-emerald-600" />
          ) : (
            <XCircle className="size-4 text-destructive" />
          )}
        </span>
      </button>

      {open ? (
        <div className="space-y-3 border-t px-3 py-2">
          <Section title="Arguments">
            <pre className="overflow-x-auto whitespace-pre-wrap break-words rounded bg-background p-2 text-xs">
              {JSON.stringify(args ?? {}, null, 2)}
            </pre>
          </Section>

          {error ? (
            <Section title="Error">
              <pre className="overflow-x-auto whitespace-pre-wrap break-words rounded bg-destructive/10 p-2 text-xs text-destructive">
                {error}
              </pre>
            </Section>
          ) : result ? (
            <Section title="Result">
              <pre className="max-h-64 overflow-auto whitespace-pre-wrap break-words rounded bg-background p-2 text-xs">
                {result}
              </pre>
            </Section>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}

function Section({
  title,
  children,
}: {
  title: string;
  children: React.ReactNode;
}) {
  return (
    <div>
      <p className="mb-1 text-xs font-medium text-muted-foreground">{title}</p>
      {children}
    </div>
  );
}
