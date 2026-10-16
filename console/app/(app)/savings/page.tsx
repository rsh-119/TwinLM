"use client";

import { useQuery } from "@tanstack/react-query";
import { PiggyBank } from "lucide-react";
import { useState } from "react";
import { Empty, ExportButton, PageHeader, RowsSkeleton, Stat } from "@/components/common";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Card } from "@/components/ui/card";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { ApiError, get } from "@/lib/api";
import { num, usd } from "@/lib/format";
import type { SavingsReport } from "@/lib/types";

const PERIODS: Record<string, string> = { "24": "24 hours", "168": "7 days", "720": "30 days" };

export default function SavingsPage() {
  const [hours, setHours] = useState("720");
  const savings = useQuery({
    queryKey: ["savings", hours, "project"],
    queryFn: () => get<SavingsReport>("/admin/savings", { hours, group_by: "project" }),
    retry: false,
  });
  if (savings.error instanceof ApiError && savings.error.status === 404) {
    return (
      <>
        <PageHeader title="Savings" />
        <Empty icon={PiggyBank} title="No baseline set">
          Set <code>[savings] baseline</code> in gateway.toml to the model you&apos;d otherwise send everything to (with a price),
          and TwinLM compares your real cost with it.
        </Empty>
      </>
    );
  }
  const t = savings.data?.total;
  const rows = (savings.data?.groups ?? []).map((g) => ({
    team: g.project ?? "(none)", requests: g.requests, cache_hits: g.cache_hits, paid_usd: g.actual_cost_usd,
    baseline_usd: g.baseline_cost_usd, saved_usd: g.savings_usd, saved_pct: g.savings_pct ?? "",
  }));
  return (
    <>
      <PageHeader title="Savings" description={savings.data ? `Compared with sending every request to ${savings.data.baseline}.` : undefined}>
        <Tabs value={hours} onValueChange={setHours}>
          <TabsList>{Object.entries(PERIODS).map(([k, v]) => <TabsTrigger key={k} value={k}>{v}</TabsTrigger>)}</TabsList>
        </Tabs>
      </PageHeader>
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <Stat label="Saved" value={usd(t?.savings_usd)} loading={savings.isLoading}
          hint={t?.savings_pct != null ? `${t.savings_pct}% less than the baseline` : undefined} />
        <Stat label="You paid" value={usd(t?.actual_cost_usd)} loading={savings.isLoading} />
        <Stat label="Baseline would cost" value={usd(t?.baseline_cost_usd)} loading={savings.isLoading} />
        <Stat label="Cache avoided" value={usd(t?.cache_avoided_usd)} loading={savings.isLoading} hint={t ? `${num(t.cache_hits)} cached answers` : undefined} />
      </div>
      {t && !t.complete && (
        <Alert>
          <AlertDescription>
            {t.requests - t.measured_requests} of {t.requests} requests have an unknown price or token count and are left out.
          </AlertDescription>
        </Alert>
      )}
      <Card className="gap-0 py-0">
        <div className="flex items-center border-b px-5 py-4">
          <p className="font-medium">By team</p>
          <div className="ml-auto"><ExportButton filename="savings" rows={rows} /></div>
        </div>
        {savings.isLoading ? <div className="p-5"><RowsSkeleton /></div> : (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead className="pl-5">Team</TableHead>
                <TableHead className="text-right">Requests</TableHead>
                <TableHead className="text-right">From cache</TableHead>
                <TableHead className="text-right">Paid</TableHead>
                <TableHead className="text-right">Baseline</TableHead>
                <TableHead className="pr-5 text-right">Saved</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {savings.data?.groups.map((g) => (
                <TableRow key={g.project ?? "none"}>
                  <TableCell className="pl-5 font-medium">{g.project ?? "(none)"}</TableCell>
                  <TableCell className="tabular text-right">{num(g.requests)}</TableCell>
                  <TableCell className="tabular text-right">{num(g.cache_hits)}</TableCell>
                  <TableCell className="tabular text-right">{usd(g.actual_cost_usd)}</TableCell>
                  <TableCell className="tabular text-right">{usd(g.baseline_cost_usd)}</TableCell>
                  <TableCell className="tabular pr-5 text-right font-medium text-success">
                    {usd(g.savings_usd)}{g.savings_pct != null && <span className="ml-1 text-xs text-muted-foreground">({g.savings_pct}%)</span>}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
      </Card>
    </>
  );
}
