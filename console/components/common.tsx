"use client";

import { Check, Copy, Download } from "lucide-react";
import { useState } from "react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { cn } from "@/lib/utils";
import { downloadCsv, usd } from "@/lib/format";

export function Logo({ className }: { className?: string }) {
  return (
    <span className={cn("flex items-center gap-2 font-semibold tracking-tight", className)}>
      <span className="grid size-7 place-items-center rounded-md bg-brand text-sm font-bold text-brand-foreground">T</span>
      TwinLM
    </span>
  );
}

export function PageHeader({ title, description, children }: {
  title: string;
  description?: string;
  children?: React.ReactNode;
}) {
  return (
    <div className="flex flex-wrap items-end justify-between gap-4">
      <div className="min-w-0">
        <h1 className="text-2xl font-semibold tracking-tight">{title}</h1>
        {description && <p className="mt-1 text-sm text-muted-foreground">{description}</p>}
      </div>
      {children && <div className="flex flex-wrap items-center gap-2">{children}</div>}
    </div>
  );
}

export function Stat({ label, value, hint, loading }: {
  label: string;
  value: React.ReactNode;
  hint?: React.ReactNode;
  loading?: boolean;
}) {
  return (
    <Card className="gap-0 py-5">
      <CardContent className="px-5">
        <p className="text-sm text-muted-foreground">{label}</p>
        {loading ? (
          <Skeleton className="mt-2 h-8 w-24" />
        ) : (
          <p className="tabular mt-1 text-2xl font-semibold tracking-tight">{value}</p>
        )}
        {hint && <p className="mt-1 text-xs text-muted-foreground">{hint}</p>}
      </CardContent>
    </Card>
  );
}

export function Empty({ icon: Icon, title, children }: {
  icon: React.ComponentType<{ className?: string }>;
  title: string;
  children?: React.ReactNode;
}) {
  return (
    <div className="flex flex-col items-center justify-center rounded-lg border border-dashed px-6 py-12 text-center">
      <div className="grid size-10 place-items-center rounded-full bg-muted">
        <Icon className="size-5 text-muted-foreground" />
      </div>
      <p className="mt-3 font-medium">{title}</p>
      {children && <div className="mt-1 max-w-sm text-sm text-muted-foreground">{children}</div>}
    </div>
  );
}

export function RowsSkeleton({ rows = 4 }: { rows?: number }) {
  return (
    <div className="space-y-2">
      {Array.from({ length: rows }).map((_, i) => (
        <Skeleton key={i} className="h-10 w-full" />
      ))}
    </div>
  );
}

const ROLE_STYLE: Record<string, string> = {
  admin: "border-brand/30 bg-brand/10 text-brand",
  member: "",
  service: "border-dashed",
};

export function RoleBadge({ role }: { role: string }) {
  const label = role === "admin" ? "Admin" : role === "service" ? "Service" : "Member";
  return (
    <Badge variant="outline" className={cn("font-normal", ROLE_STYLE[role])}>
      {label}
    </Badge>
  );
}

export function StatusDot({ tone }: { tone: "ok" | "warn" | "down" | "off" }) {
  const color = { ok: "bg-success", warn: "bg-warning", down: "bg-destructive", off: "bg-muted-foreground/40" }[tone];
  return <span className={cn("inline-block size-2 shrink-0 rounded-full", color)} aria-hidden />;
}

export function CopyButton({ value, label = "Copy" }: { value: string; label?: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <Button
      type="button"
      variant="outline"
      size="sm"
      onClick={async () => {
        await navigator.clipboard.writeText(value);
        setCopied(true);
        setTimeout(() => setCopied(false), 1500);
      }}
    >
      {copied ? <Check /> : <Copy />}
      {copied ? "Copied" : label}
    </Button>
  );
}

/** A secret shown exactly once (new key, temporary password). */
export function SecretReveal({ title, value, children }: { title: string; value: string; children?: React.ReactNode }) {
  return (
    <div className="space-y-3 rounded-lg border border-brand/30 bg-brand/5 p-4">
      <p className="text-sm font-medium">{title}</p>
      <div className="flex items-center gap-2">
        <code className="min-w-0 flex-1 truncate rounded-md border bg-background px-3 py-2 font-mono text-sm">{value}</code>
        <CopyButton value={value} />
      </div>
      {children && <p className="text-xs text-muted-foreground">{children}</p>}
    </div>
  );
}

export function ExportButton({ filename, rows }: { filename: string; rows: Record<string, unknown>[] }) {
  return (
    <Button variant="outline" size="sm" disabled={!rows.length} onClick={() => downloadCsv(filename, rows)}>
      <Download />
      Export
    </Button>
  );
}

/** Spend against a budget: green, amber from 80%, red at 100%. */
export function BudgetBar({ spent, limit, reached = false }: { spent: number; limit: number; reached?: boolean }) {
  const used = limit > 0 ? spent / limit : 0;
  const tone = reached || used >= 1 ? "bg-destructive" : used >= 0.8 ? "bg-warning" : "bg-success";
  return (
    <div className="space-y-1.5">
      <div className="flex items-baseline justify-between gap-3 text-sm">
        <span className="font-mono">{usd(spent)} <span className="text-muted-foreground">of {usd(limit)}</span></span>
        <span className={cn("font-mono text-xs", reached ? "font-medium text-destructive" : "text-muted-foreground")}>
          {reached ? "Limit reached" : `${Math.round(used * 100)}%`}
        </span>
      </div>
      <div className="h-1.5 overflow-hidden rounded-full bg-muted" role="progressbar" aria-valuemin={0} aria-valuemax={100}
        aria-valuenow={Math.min(100, Math.round(used * 100))} aria-label={`${Math.round(used * 100)}% of budget used`}>
        <div className={`h-full rounded-full ${tone}`} style={{ width: `${Math.min(100, used * 100)}%` }} />
      </div>
    </div>
  );
}
