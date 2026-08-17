"use client";

import { Loader2, MessageSquare } from "lucide-react";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { useLogin, useRegister } from "@/hooks/use-auth";

export default function LoginPage() {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [displayName, setDisplayName] = useState("");

  const login = useLogin();
  const register = useRegister();
  const pending = login.isPending || register.isPending;
  const error = login.error ?? register.error;

  return (
    <main className="flex min-h-screen items-center justify-center bg-muted/30 p-4">
      <div className="w-full max-w-md space-y-6">
        <div className="flex flex-col items-center gap-2 text-center">
          <div className="flex size-12 items-center justify-center rounded-xl bg-primary text-primary-foreground">
            <MessageSquare className="size-6" />
          </div>
          <h1 className="text-2xl font-semibold tracking-tight">MCP Host</h1>
          <p className="text-sm text-muted-foreground">
            Chat with an AI that can use your connected tools.
          </p>
        </div>

        <Card>
          <Tabs defaultValue="login">
            <CardHeader>
              <TabsList className="grid w-full grid-cols-2">
                <TabsTrigger value="login">Sign in</TabsTrigger>
                <TabsTrigger value="register">Create account</TabsTrigger>
              </TabsList>
            </CardHeader>

            <CardContent>
              <TabsContent value="login" className="mt-0">
                <form
                  className="space-y-4"
                  onSubmit={(e) => {
                    e.preventDefault();
                    login.mutate({ email, password });
                  }}
                >
                  <Field
                    id="login-email"
                    label="Email"
                    type="email"
                    value={email}
                    onChange={setEmail}
                    placeholder="you@example.com"
                  />
                  <Field
                    id="login-password"
                    label="Password"
                    type="password"
                    value={password}
                    onChange={setPassword}
                    placeholder="••••••••"
                  />
                  <SubmitButton pending={pending} label="Sign in" />
                </form>
              </TabsContent>

              <TabsContent value="register" className="mt-0">
                <form
                  className="space-y-4"
                  onSubmit={(e) => {
                    e.preventDefault();
                    register.mutate({ email, password, displayName });
                  }}
                >
                  <Field
                    id="reg-name"
                    label="Name"
                    value={displayName}
                    onChange={setDisplayName}
                    placeholder="Ada Lovelace"
                    required={false}
                  />
                  <Field
                    id="reg-email"
                    label="Email"
                    type="email"
                    value={email}
                    onChange={setEmail}
                    placeholder="you@example.com"
                  />
                  <Field
                    id="reg-password"
                    label="Password"
                    type="password"
                    value={password}
                    onChange={setPassword}
                    placeholder="At least 8 characters"
                    minLength={8}
                  />
                  <SubmitButton pending={pending} label="Create account" />
                </form>
              </TabsContent>

              {error ? (
                <p className="mt-4 rounded-md bg-destructive/10 px-3 py-2 text-sm text-destructive">
                  {error.message}
                </p>
              ) : null}
            </CardContent>
          </Tabs>
        </Card>
      </div>
    </main>
  );
}

function Field({
  id,
  label,
  value,
  onChange,
  type = "text",
  placeholder,
  required = true,
  minLength,
}: {
  id: string;
  label: string;
  value: string;
  onChange: (value: string) => void;
  type?: string;
  placeholder?: string;
  required?: boolean;
  minLength?: number;
}) {
  return (
    <div className="space-y-2">
      <Label htmlFor={id}>{label}</Label>
      <Input
        id={id}
        type={type}
        value={value}
        placeholder={placeholder}
        required={required}
        minLength={minLength}
        onChange={(e) => onChange(e.target.value)}
      />
    </div>
  );
}

function SubmitButton({
  pending,
  label,
}: {
  pending: boolean;
  label: string;
}) {
  return (
    <Button type="submit" className="w-full" disabled={pending}>
      {pending ? <Loader2 className="size-4 animate-spin" /> : null}
      {label}
    </Button>
  );
}
