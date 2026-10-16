"use client";

import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { ExportButton, PageHeader, RowsSkeleton, Stat } from "@/components/common";
import { SpendChart } from "@/components/spend-chart";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { get } from "@/lib/api";
import { num, pct, usd } from "@/lib/format";
import type { DailyPoint, UsageGroup, UsageReport } from "@/lib/types";

const PERIODS = { "1": 1, "24": 24, "168": 168, "720": 720 } as const;
const LABEL: Record<string, string> = { "1": "1 hour", "24": "24 hours", "168": "7 days", "720": "30 days" };
const GROUPS = { project: "Team", model: "Model", provider: "Provider", api_key_id: "Key" } as const;

function groupLabel(g: UsageGroup, by: string): string {
  if (by === "model") return `${g.provider ?? "?"}/${g.model ?? "?"}`;
  const v = g[by as keyof UsageGroup];
  return v === null || v === undefined ? "(none)" : String(v);
}

export default function UsagePage() {
  const [hours, setHours] = useState<keyof typeof PERIODS>("720");
  const [by, setBy] = useState<keyof typeof GROUPS>("project");
  const usage = useQuery({
    queryKey: ["usage", hours, by],
    queryFn: () => get<UsageReport>("/admin/usage", { hours: PERIODS[hours], group_by: by }),
  });
  const days = Math.max(1, Math.ceil(PERIODS[hours] / 24));
  const daily = useQuery({ queryKey: ["daily", days], queryFn: () => get<{ days: DailyPoint[] }>("/admin/usage/daily", { days }) });
  const t = usage.data?.total;
  const groups = [...(usage.data?.groups ?? [])].sort((a, b) => (b.cost_usd ?? 0) - (a.cost_usd ?? 0) || b.requests - a.requests);
  const rows = groups.map((g) => ({
    [GROUPS[by].toLowerCase()]: groupLabel(g, by), requests: g.requests, errors: g.errors, cache_hits: g.cache_hits,
    tokens: g.total_tokens, cost_usd: g.cost_usd ?? "", avg_latency_ms: g.avg_latency_ms ?? "",
  }));
  return (
    <>
      <PageHeader title="Usage" description="Requests, tokens and cost across the company.">
        <Tabs value={hours} onValueChange={(v) => setHours(v as keyof typeof PERIODS)}>
          <TabsList>{Object.keys(PERIODS).map((k) => <TabsTrigger key={k} value={k}>{LABEL[k]}</TabsTrigger>)}</TabsList>
        </Tabs>
      </PageHeader>
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <Stat label="Requests" value={num(t?.requests)} loading={usage.isLoading} hint={t ? `${num(t.errors)} errors` : undefined} />
        <Stat label="Cost" value={usd(t?.cost_usd ?? (t?.requests === 0 ? 0 : null))} loading={usage.isLoading} />
        <Stat label="Cache hit rate" value={pct(t?.cache_hit_rate)} loading={usage.isLoading} />
        <Stat label="Fallbacks" value={num(t?.fallbacks)} loading={usage.isLoading} hint="Answered by a backup provider" />
      </div>
      {t && !t.cost_complete && (
        <Alert>
          <AlertDescription>{t.unpriced_requests} requests use models without a price, so their cost is unknown (not $0). Add prices on the Models page.</AlertDescription>
        </Alert>
      )}
      {hours !== "1" && (
        <Card>
          <CardHeader><CardTitle>Spend per day</CardTitle></CardHeader>
          <CardContent>{daily.data ? <SpendChart points={daily.data.days} days={days} /> : <RowsSkeleton rows={5} />}</CardContent>
        </Card>
      )}
      <Card className="gap-0 py-0">
        <div className="flex flex-wrap items-center gap-3 border-b px-5 py-4">
          <p className="font-medium">By {GROUPS[by].toLowerCase()}</p>
          <div className="ml-auto flex gap-2">
            <Select value={by} onValueChange={(v) => setBy(v as keyof typeof GROUPS)}>
              <SelectTrigger className="w-36" size="sm"><SelectValue /></SelectTrigger>
              <SelectContent>{Object.entries(GROUPS).map(([k, v]) => <SelectItem key={k} value={k}>{v}</SelectItem>)}</SelectContent>
            </Select>
            <ExportButton filename={`usage-${by}`} rows={rows} />
          </div>
        </div>
        {usage.isLoading ? <div className="p-5"><RowsSkeleton /></div> : !groups.length ? (
          <p className="p-5 text-sm text-muted-foreground">No requests in this period.</p>
        ) : (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead className="pl-5">{GROUPS[by]}</TableHead>
                <TableHead className="text-right">Requests</TableHead>
                <TableHead className="text-right">Errors</TableHead>
                <TableHead className="text-right">From cache</TableHead>
                <TableHead className="text-right">Tokens</TableHead>
                <TableHead className="text-right">Avg latency</TableHead>
                <TableHead className="pr-5 text-right">Cost</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {groups.map((g) => (
                <TableRow key={groupLabel(g, by)}>
                  <TableCell className="max-w-64 truncate pl-5 font-mono text-xs">{groupLabel(g, by)}</TableCell>
                  <TableCell className="tabular text-right">{num(g.requests)}</TableCell>
                  <TableCell className="tabular text-right">{num(g.errors)}</TableCell>
                  <TableCell className="tabular text-right">{num(g.cache_hits)}</TableCell>
                  <TableCell className="tabular text-right">{num(g.total_tokens)}</TableCell>
                  <TableCell className="tabular text-right">{g.avg_latency_ms != null ? `${Math.round(g.avg_latency_ms)} ms` : "—"}</TableCell>
                  <TableCell className="tabular pr-5 text-right font-medium">{usd(g.cost_usd)}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
      </Card>
    </>
  );
}
