"use client";

import { useQuery } from "@tanstack/react-query";
import { RefreshCw, Trash2 } from "lucide-react";
import { PageHeader, RowsSkeleton, StatusDot } from "@/components/common";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { del, get } from "@/lib/api";
import { num, pct } from "@/lib/format";
import { useAction } from "@/lib/hooks";
import type { ProviderHealth } from "@/lib/types";

const STATE = {
  closed: { tone: "ok", label: "Healthy" },
  half_open: { tone: "warn", label: "Recovering" },
  open: { tone: "down", label: "Down — skipped" },
} as const;

export default function HealthPage() {
  const health = useQuery({
    queryKey: ["health"],
    queryFn: () => get<ProviderHealth[]>("/admin/providers"),
    refetchInterval: 10_000,
  });
  const clear = useAction(() => del<{ cleared: number }>("/admin/cache"), { success: (r) => `Cleared ${r.cleared} cached answers` });
  return (
    <>
      <PageHeader title="Health" description="Provider status on this gateway, refreshed every 10 seconds.">
        <Button variant="outline" onClick={() => health.refetch()}><RefreshCw /> Refresh</Button>
      </PageHeader>
      <Card className="py-0">
        {health.isLoading ? <div className="p-5"><RowsSkeleton /></div> : (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead className="pl-5">Provider</TableHead>
                <TableHead>Status</TableHead>
                <TableHead className="text-right">Requests</TableHead>
                <TableHead className="text-right">Error rate</TableHead>
                <TableHead className="text-right">Latency</TableHead>
                <TableHead className="text-right">In flight</TableHead>
                <TableHead className="pr-5">Last error</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {health.data?.map((p) => (
                <TableRow key={p.provider}>
                  <TableCell className="pl-5 font-medium">{p.provider}</TableCell>
                  <TableCell>
                    <span className="flex items-center gap-2"><StatusDot tone={STATE[p.state].tone} />{STATE[p.state].label}</span>
                  </TableCell>
                  <TableCell className="tabular text-right">{num(p.total_requests)}</TableCell>
                  <TableCell className="tabular text-right">{pct(p.error_rate, 1)}</TableCell>
                  <TableCell className="tabular text-right">{p.latency_ewma_ms != null ? `${Math.round(p.latency_ewma_ms)} ms` : "—"}</TableCell>
                  <TableCell className="tabular text-right">{num(p.in_flight)}</TableCell>
                  <TableCell className="max-w-72 truncate pr-5 text-xs text-muted-foreground" title={p.last_error ?? ""}>
                    {p.last_error ?? "—"}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
      </Card>
      <Card>
        <CardHeader>
          <CardTitle>Response cache</CardTitle>
          <CardDescription>Identical deterministic requests are answered from memory for $0. Clear it after changing models or prompts.</CardDescription>
        </CardHeader>
        <CardContent>
          <Button variant="outline" onClick={() => clear.mutate(undefined)} disabled={clear.isPending}><Trash2 /> Clear cache</Button>
        </CardContent>
      </Card>
    </>
  );
}
