"use client";

import { useQueries, useQuery } from "@tanstack/react-query";
import { ArrowRight, KeyRound, MessageSquare, Users } from "lucide-react";
import Link from "next/link";
import { CopyButton, Empty, PageHeader, RoleBadge, RowsSkeleton, Stat } from "@/components/common";
import { SpendChart } from "@/components/spend-chart";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { ApiError, get } from "@/lib/api";
import { num, pct, plural, usd } from "@/lib/format";
import { useMe, useTeams } from "@/lib/hooks";
import type { DailyPoint, SavingsReport, TeamSummary, TeamUsage, UsageReport } from "@/lib/types";

function TopModels({ groups }: { groups: UsageReport["groups"] }) {
  const top = [...groups].sort((a, b) => b.requests - a.requests).slice(0, 5);
  const max = Math.max(1, ...top.map((g) => g.requests));
  if (!top.length) return <p className="text-sm text-muted-foreground">No requests yet.</p>;
  return (
    <ul className="space-y-3">
      {top.map((g) => (
        <li key={`${g.provider}/${g.model}`} className="space-y-1">
          <div className="flex items-baseline justify-between gap-3 text-sm">
            <span className="truncate font-mono text-xs">{g.provider}/{g.model}</span>
            <span className="tabular shrink-0 text-muted-foreground">{num(g.requests)} · {usd(g.cost_usd)}</span>
          </div>
          <div className="h-1.5 rounded-full bg-muted">
            <div className="h-1.5 rounded-full bg-brand" style={{ width: `${(g.requests / max) * 100}%` }} />
          </div>
        </li>
      ))}
    </ul>
  );
}

function AdminOverview() {
  const usage = useQuery({ queryKey: ["usage", 720, "model"], queryFn: () => get<UsageReport>("/admin/usage", { hours: 720, group_by: "model" }) });
  const daily = useQuery({ queryKey: ["daily", 30], queryFn: () => get<{ days: DailyPoint[] }>("/admin/usage/daily", { days: 30 }) });
  const savings = useQuery({
    queryKey: ["savings", 720, "project"],
    queryFn: () => get<SavingsReport>("/admin/savings", { hours: 720 }),
    retry: false,
  });
  const t = usage.data?.total;
  const noBaseline = savings.error instanceof ApiError && savings.error.status === 404;
  return (
    <>
      <PageHeader title="Overview" description="Your company's AI usage over the last 30 days.">
        <Button asChild variant="outline">
          <Link href="/playground/"><MessageSquare /> Playground</Link>
        </Button>
      </PageHeader>
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <Stat label="Requests" value={num(t?.requests)} loading={usage.isLoading}
          hint={t ? `${num(t.errors)} errors · ${num(t.fallbacks)} fallbacks` : undefined} />
        <Stat label="Spend" value={usd(t?.cost_usd ?? (t?.requests === 0 ? 0 : null))} loading={usage.isLoading}
          hint={t && !t.cost_complete ? `${t.unpriced_requests} requests have no price` : "Known cost from your prices"} />
        <Stat label="Saved" loading={savings.isLoading}
          value={noBaseline ? "—" : usd(savings.data?.total.savings_usd)}
          hint={noBaseline ? "Set a savings baseline in gateway.toml"
            : savings.data?.total.savings_pct != null ? `${savings.data.total.savings_pct}% vs ${savings.data.baseline}` : undefined} />
        <Stat label="Cache hit rate" value={pct(t?.cache_hit_rate)} loading={usage.isLoading}
          hint={t ? `${num(t.cache_hits)} answers served for $0` : undefined} />
      </div>
      <div className="grid gap-4 lg:grid-cols-3">
        <Card className="lg:col-span-2">
          <CardHeader>
            <CardTitle>Spend per day</CardTitle>
            <CardDescription>USD, from the prices you configured</CardDescription>
          </CardHeader>
          <CardContent>{daily.data ? <SpendChart points={daily.data.days} /> : <RowsSkeleton rows={5} />}</CardContent>
        </Card>
        <Card>
          <CardHeader>
            <CardTitle>Top models</CardTitle>
            <CardDescription>By requests, 30 days</CardDescription>
          </CardHeader>
          <CardContent>{usage.data ? <TopModels groups={usage.data.groups} /> : <RowsSkeleton />}</CardContent>
        </Card>
      </div>
    </>
  );
}

/** First steps for a team without keys (e.g. right after signing up). */
function GetStarted({ team }: { team: TeamSummary }) {
  const base = typeof window === "undefined" ? "" : `${window.location.origin}/v1`;
  const snippet = `client = OpenAI(base_url="${base}", api_key="gw_live_...")`;
  return (
    <Card className="border-brand/40">
      <CardHeader>
        <CardTitle>Get started</CardTitle>
        <CardDescription>Three steps to send your app&apos;s AI requests through TwinLM.</CardDescription>
      </CardHeader>
      <CardContent>
        <ol className="space-y-5 text-sm">
          <li className="space-y-2">
            <p><span className="font-medium">1. Create an API key</span> for your app on your team&apos;s page. It&apos;s shown once.</p>
            <Button asChild size="sm">
              <Link href={`/teams/view/?id=${team.id}&tab=keys`}><KeyRound /> Create a key</Link>
            </Button>
          </li>
          <li className="space-y-2">
            <p><span className="font-medium">2. Point your app at TwinLM.</span> Any OpenAI SDK or framework works; change two settings:</p>
            <div className="flex items-start gap-2 rounded-md border bg-muted/50 p-3">
              <code className="min-w-0 flex-1 break-all font-mono text-xs">{snippet}</code>
              <CopyButton value={snippet} />
            </div>
            <p className="text-muted-foreground">
              Use <code className="font-mono text-xs">model=&quot;auto&quot;</code> to let TwinLM pick the cheapest model that fits.
            </p>
          </li>
          <li className="space-y-2">
            <p><span className="font-medium">3. Or try it right here</span>, then watch requests and spend appear on this page.</p>
            <Button asChild size="sm" variant="outline">
              <Link href="/playground/"><MessageSquare /> Open playground</Link>
            </Button>
          </li>
        </ol>
      </CardContent>
    </Card>
  );
}

function MemberOverview() {
  const teams = useTeams();
  const usage = useQueries({
    queries: (teams.data ?? []).map((team) => ({
      queryKey: ["team-usage", team.id],
      queryFn: () => get<TeamUsage>(`/console/teams/${team.id}/usage`),
    })),
  });
  const total = usage.reduce(
    (acc, q) => ({
      requests: acc.requests + (q.data?.total.requests ?? 0),
      cost: acc.cost + (q.data?.total.cost_usd ?? 0),
      cache: acc.cache + (q.data?.total.cache_hits ?? 0),
    }),
    { requests: 0, cost: 0, cache: 0 },
  );
  return (
    <>
      <PageHeader title="Overview" description="Your usage over the last 30 days.">
        <Button asChild>
          <Link href="/playground/"><MessageSquare /> Open playground</Link>
        </Button>
      </PageHeader>
      <div className="grid gap-4 sm:grid-cols-3">
        <Stat label="Your requests" value={num(total.requests)} loading={teams.isLoading} />
        <Stat label="Your spend" value={usd(total.cost)} loading={teams.isLoading} />
        <Stat label="Answered from cache" value={num(total.cache)} loading={teams.isLoading} hint="Served for $0" />
      </div>
      {teams.data?.length === 1 && teams.data[0].active_keys === 0 && teams.data[0].my_role === "admin" && (
        <GetStarted team={teams.data[0]} />
      )}
      <Card>
        <CardHeader>
          <CardTitle>Your teams</CardTitle>
          <CardDescription>Create keys and see usage on each team&apos;s page.</CardDescription>
        </CardHeader>
        <CardContent>
          {teams.isLoading ? <RowsSkeleton rows={2} /> : !teams.data?.length ? (
            <Empty icon={Users} title="You're not in a team yet">Ask an admin to add you to a team.</Empty>
          ) : (
            <ul className="divide-y">
              {teams.data.map((team) => (
                <li key={team.id}>
                  <Link href={`/teams/view/?id=${team.id}`} className="flex items-center gap-3 py-3 hover:text-brand">
                    <span className="font-medium">{team.name}</span>
                    <RoleBadge role={team.my_role} />
                    <span className="ml-auto text-sm text-muted-foreground">{plural(team.members, "member")}</span>
                    <ArrowRight className="size-4 text-muted-foreground" />
                  </Link>
                </li>
              ))}
            </ul>
          )}
        </CardContent>
      </Card>
    </>
  );
}

export default function OverviewPage() {
  const { data: me } = useMe();
  return me?.role === "admin" ? <AdminOverview /> : <MemberOverview />;
}
