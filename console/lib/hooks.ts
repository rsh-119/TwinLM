"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { get } from "@/lib/api";
import type { Me, TeamSummary } from "@/lib/types";

export function useMe() {
  return useQuery({ queryKey: ["me"], queryFn: () => get<Me>("/auth/me"), staleTime: 60_000 });
}

export function useTeams(enabled = true) {
  return useQuery({ queryKey: ["teams"], queryFn: () => get<TeamSummary[]>("/console/teams"), enabled });
}

/** A mutation that toasts its result and refreshes the given queries. */
export function useAction<TVars, TResult = unknown>(
  fn: (vars: TVars) => Promise<TResult>,
  opts: { success?: string | ((r: TResult) => string); invalidate?: unknown[][]; onSuccess?: (r: TResult) => void } = {},
) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: fn,
    onSuccess: (result) => {
      if (opts.success) toast.success(typeof opts.success === "function" ? opts.success(result) : opts.success);
      for (const key of opts.invalidate ?? []) qc.invalidateQueries({ queryKey: key });
      opts.onSuccess?.(result);
    },
    onError: (error: Error) => toast.error(error.message),
  });
}
