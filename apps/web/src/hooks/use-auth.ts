"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useRouter } from "next/navigation";

import { ApiError, api } from "@/lib/api";
import type { User } from "@/lib/types";

export function useCurrentUser() {
  return useQuery<User | null>({
    queryKey: ["me"],
    queryFn: async () => {
      try {
        return await api.me();
      } catch (error) {
        // A 401 here is the normal "signed out" state, not a failure.
        if (error instanceof ApiError && error.status === 401) return null;
        throw error;
      }
    },
    staleTime: 60_000,
  });
}

export function useLogin() {
  const queryClient = useQueryClient();
  const router = useRouter();

  return useMutation({
    mutationFn: ({ email, password }: { email: string; password: string }) =>
      api.login(email, password),
    onSuccess: (session) => {
      queryClient.setQueryData(["me"], session.user);
      router.push("/chat");
    },
  });
}

export function useRegister() {
  const queryClient = useQueryClient();
  const router = useRouter();

  return useMutation({
    mutationFn: ({
      email,
      password,
      displayName,
    }: {
      email: string;
      password: string;
      displayName?: string;
    }) => api.register(email, password, displayName),
    onSuccess: (session) => {
      queryClient.setQueryData(["me"], session.user);
      router.push("/chat");
    },
  });
}

export function useLogout() {
  const queryClient = useQueryClient();
  const router = useRouter();

  return useMutation({
    mutationFn: () => api.logout(),
    onSuccess: () => {
      queryClient.clear();
      router.push("/login");
    },
  });
}
