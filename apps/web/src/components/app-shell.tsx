"use client";

import { LogOut, MessageSquare, Plug, Settings } from "lucide-react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect } from "react";

import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { useCurrentUser, useLogout } from "@/hooks/use-auth";
import { cn } from "@/lib/utils";

/**
 * Authenticated layout frame.
 *
 * Redirects to /login once we know for certain the visitor is signed out,
 * rather than on the first render, so a page refresh does not flash the login
 * screen while `/api/auth/me` is still in flight.
 */
export function AppShell({ children }: { children: React.ReactNode }) {
  const { data: user, isLoading } = useCurrentUser();
  const logout = useLogout();
  const router = useRouter();
  const pathname = usePathname();

  useEffect(() => {
    if (!isLoading && user === null) router.replace("/login");
  }, [isLoading, user, router]);

  if (isLoading) {
    return (
      <div className="flex min-h-screen">
        <Skeleton className="h-screen w-16" />
        <div className="flex-1 p-8">
          <Skeleton className="h-8 w-64" />
        </div>
      </div>
    );
  }

  if (!user) return null;

  const navigation = [
    { href: "/chat", icon: MessageSquare, label: "Chat" },
    { href: "/settings/connectors", icon: Plug, label: "Connectors" },
    { href: "/settings", icon: Settings, label: "Settings" },
  ];

  return (
    <div className="flex h-screen overflow-hidden bg-background">
      <nav className="flex w-16 shrink-0 flex-col items-center gap-2 border-r bg-muted/30 py-4">
        <div className="mb-4 flex size-9 items-center justify-center rounded-lg bg-primary text-primary-foreground">
          <MessageSquare className="size-5" />
        </div>

        {navigation.map((item) => {
          const active =
            item.href === "/settings"
              ? pathname === "/settings"
              : pathname.startsWith(item.href);
          return (
            <Link
              key={item.href}
              href={item.href}
              title={item.label}
              aria-label={item.label}
              className={cn(
                "flex size-10 items-center justify-center rounded-lg transition-colors",
                active
                  ? "bg-primary/10 text-primary"
                  : "text-muted-foreground hover:bg-accent hover:text-foreground",
              )}
            >
              <item.icon className="size-5" />
            </Link>
          );
        })}

        <div className="mt-auto flex flex-col items-center gap-2">
          <div
            className="flex size-9 items-center justify-center rounded-full bg-accent text-xs font-medium"
            title={user.email}
          >
            {(user.display_name ?? user.email).slice(0, 2).toUpperCase()}
          </div>
          <Button
            variant="ghost"
            size="icon"
            title="Sign out"
            aria-label="Sign out"
            onClick={() => logout.mutate()}
          >
            <LogOut className="size-4" />
          </Button>
        </div>
      </nav>

      <div className="flex min-w-0 flex-1 flex-col">{children}</div>
    </div>
  );
}
