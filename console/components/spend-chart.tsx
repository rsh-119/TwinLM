"use client";

import { Area, AreaChart, CartesianGrid, XAxis, YAxis } from "recharts";
import { ChartContainer, ChartTooltip, ChartTooltipContent, type ChartConfig } from "@/components/ui/chart";
import type { DailyPoint } from "@/lib/types";

const config = {
  cost_usd: { label: "Spend (USD)", color: "var(--chart-1)" },
  requests: { label: "Requests", color: "var(--chart-2)" },
} satisfies ChartConfig;

/** Fills missing days with zeros so the line doesn't skip gaps. */
function fill(points: DailyPoint[], days: number): DailyPoint[] {
  const byDate = new Map(points.map((p) => [p.date, p]));
  const out: DailyPoint[] = [];
  const today = new Date();
  for (let i = days - 1; i >= 0; i--) {
    const d = new Date(Date.UTC(today.getUTCFullYear(), today.getUTCMonth(), today.getUTCDate() - i));
    const key = d.toISOString().slice(0, 10);
    out.push(byDate.get(key) ?? { date: key, requests: 0, cost_usd: 0, cache_hits: 0 });
  }
  return out;
}

export function SpendChart({ points, days = 30, metric = "cost_usd" }: {
  points: DailyPoint[];
  days?: number;
  metric?: "cost_usd" | "requests";
}) {
  const data = fill(points, days);
  return (
    <ChartContainer config={config} className="aspect-auto h-64 w-full">
      <AreaChart data={data} margin={{ left: 4, right: 8, top: 8 }}>
        <defs>
          <linearGradient id={`fill-${metric}`} x1="0" y1="0" x2="0" y2="1">
            <stop offset="5%" stopColor={`var(--color-${metric})`} stopOpacity={0.3} />
            <stop offset="95%" stopColor={`var(--color-${metric})`} stopOpacity={0.02} />
          </linearGradient>
        </defs>
        <CartesianGrid vertical={false} />
        <XAxis
          dataKey="date"
          tickLine={false}
          axisLine={false}
          tickMargin={8}
          minTickGap={28}
          tickFormatter={(v: string) => new Date(v + "T00:00:00Z").toLocaleDateString("en-US", { month: "short", day: "numeric", timeZone: "UTC" })}
        />
        <YAxis
          tickLine={false}
          axisLine={false}
          width={48}
          tickFormatter={(v: number) => (metric === "cost_usd" ? `$${v < 10 ? v.toFixed(2) : Math.round(v)}` : String(v))}
        />
        <ChartTooltip cursor={false} content={<ChartTooltipContent indicator="line" />} />
        <Area dataKey={metric} type="monotone" stroke={`var(--color-${metric})`} strokeWidth={2} fill={`url(#fill-${metric})`} />
      </AreaChart>
    </ChartContainer>
  );
}
