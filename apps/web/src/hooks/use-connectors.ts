"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";

import { api } from "@/lib/api";
import type { Connector } from "@/lib/types";

const CONNECTORS_KEY = ["connectors"];

export function useConnectors() {
  return useQuery<Connector[]>({
    queryKey: CONNECTORS_KEY,
    queryFn: api.listConnectors,
  });
}

export function useConnect() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (connectorId: string) => api.connect(connectorId),
    onSuccess: (result) => {
      // OAuth connectors hand back a URL; leaving the app is the whole point,
      // so navigate rather than opening a popup (popups get blocked).
      if (result.authorization_url) {
        window.location.href = result.authorization_url;
        return;
      }
      toast.success("Connected");
      queryClient.invalidateQueries({ queryKey: CONNECTORS_KEY });
    },
    onError: (error: Error) => toast.error(error.message),
  });
}

export function useSetConnectionEnabled() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: ({
      connectionId,
      enabled,
    }: {
      connectionId: string;
      enabled: boolean;
    }) => api.setConnectionEnabled(connectionId, enabled),

    // Optimistic: the toggle should feel instant.
    onMutate: async ({ connectionId, enabled }) => {
      await queryClient.cancelQueries({ queryKey: CONNECTORS_KEY });
      const previous = queryClient.getQueryData<Connector[]>(CONNECTORS_KEY);

      queryClient.setQueryData<Connector[]>(CONNECTORS_KEY, (old) =>
        old?.map((connector) =>
          connector.connection?.id === connectionId
            ? {
                ...connector,
                connection: { ...connector.connection, is_enabled: enabled },
              }
            : connector,
        ),
      );
      return { previous };
    },
    onError: (error: Error, _vars, context) => {
      queryClient.setQueryData(CONNECTORS_KEY, context?.previous);
      toast.error(error.message);
    },
    onSettled: () =>
      queryClient.invalidateQueries({ queryKey: CONNECTORS_KEY }),
  });
}

export function useSetEnabledTools() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: ({
      connectionId,
      toolNames,
    }: {
      connectionId: string;
      toolNames: string[] | null;
    }) => api.setEnabledTools(connectionId, toolNames),
    onSuccess: () =>
      queryClient.invalidateQueries({ queryKey: CONNECTORS_KEY }),
    onError: (error: Error) => toast.error(error.message),
  });
}

export function useRefreshTools() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (connectionId: string) => api.refreshTools(connectionId),
    onSuccess: (connection) => {
      toast.success(`Found ${connection.tools.length} tools`);
      queryClient.invalidateQueries({ queryKey: CONNECTORS_KEY });
    },
    onError: (error: Error) => toast.error(error.message),
  });
}

export function useDisconnect() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (connectionId: string) => api.disconnect(connectionId),
    onSuccess: () => {
      toast.success("Disconnected and credentials removed");
      queryClient.invalidateQueries({ queryKey: CONNECTORS_KEY });
    },
    onError: (error: Error) => toast.error(error.message),
  });
}

export function useDeleteConnection() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (connectionId: string) => api.deleteConnection(connectionId),
    onSuccess: () => {
      toast.success("Connection deleted");
      queryClient.invalidateQueries({ queryKey: CONNECTORS_KEY });
    },
    onError: (error: Error) => toast.error(error.message),
  });
}
