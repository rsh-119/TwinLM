"use client";

import { useQuery } from "@tanstack/react-query";
import { BellRing, Pencil, Plus, Trash2, Wallet } from "lucide-react";
import { useState } from "react";
import { BudgetBar, Empty, PageHeader, RowsSkeleton } from "@/components/common";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle, DialogTrigger,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { del, get, put } from "@/lib/api";
import { ago, usd } from "@/lib/format";
import { useAction, useTeams } from "@/lib/hooks";
import type { BudgetAlert, BudgetStatus, Person } from "@/lib/types";

type Form = { scope: "org" | "team" | "user"; target: string; period: "daily" | "monthly"; limit_usd: string };
const EMPTY: Form = { scope: "team", target: "", period: "monthly", limit_usd: "" };

function name(b: BudgetStatus) {
  if (b.scope === "org") return "Whole company";
  return b.scope === "team" ? b.target : b.target_name ?? b.target;
}

function resets(iso: string) {
  return new Date(iso).toLocaleDateString("en-US", { month: "short", day: "numeric", timeZone: "UTC" });
}

function BudgetDialog({ initial, trigger }: { initial?: Form; trigger: React.ReactNode }) {
  const [open, setOpen] = useState(false);
  const [form, setForm] = useState<Form>(initial ?? EMPTY);
  const teams = useTeams(open);
  const people = useQuery({ queryKey: ["people"], queryFn: () => get<Person[]>("/admin/users"), enabled: open });
  const save = useAction((f: Form) => put("/admin/budgets", { ...f, limit_usd: Number(f.limit_usd) }), {
    success: "Budget saved", invalidate: [["budgets"]], onSuccess: () => setOpen(false),
  });
  const editing = !!initial;
  const ready = Number(form.limit_usd) > 0 && (form.scope === "org" || !!form.target);
  return (
    <Dialog open={open} onOpenChange={(o) => { setOpen(o); if (o) setForm(initial ?? EMPTY); }}>
      <DialogTrigger asChild>{trigger}</DialogTrigger>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{editing ? "Change budget" : "Add a budget"}</DialogTitle>
          <DialogDescription>
            Requests that would take spend over the limit are refused until the period resets. Alerts go out at 80% and 100%.
          </DialogDescription>
        </DialogHeader>
        <form onSubmit={(e) => { e.preventDefault(); save.mutate(form); }} className="space-y-4">
          <div className="grid gap-4 sm:grid-cols-2">
            <div className="space-y-2">
              <Label>Applies to</Label>
              <Select value={form.scope} disabled={editing}
                onValueChange={(scope) => setForm({ ...form, scope: scope as Form["scope"], target: "" })}>
                <SelectTrigger className="w-full"><SelectValue /></SelectTrigger>
                <SelectContent>
                  <SelectItem value="team">A team</SelectItem>
                  <SelectItem value="user">A person</SelectItem>
                  <SelectItem value="org">Whole company</SelectItem>
                </SelectContent>
              </Select>
            </div>
            {form.scope !== "org" && (
              <div className="space-y-2">
                <Label>{form.scope === "team" ? "Team" : "Person"}</Label>
                <Select value={form.target} disabled={editing} onValueChange={(target) => setForm({ ...form, target })}>
                  <SelectTrigger className="w-full"><SelectValue placeholder="Choose…" /></SelectTrigger>
                  <SelectContent>
                    {form.scope === "team"
                      ? teams.data?.map((t) => <SelectItem key={t.id} value={t.name}>{t.name}</SelectItem>)
                      : people.data?.map((p) => <SelectItem key={p.id} value={p.id}>{p.name}</SelectItem>)}
                  </SelectContent>
                </Select>
              </div>
            )}
          </div>
          <div className="grid gap-4 sm:grid-cols-2">
            <div className="space-y-2">
              <Label>Period</Label>
              <Select value={form.period} disabled={editing}
                onValueChange={(period) => setForm({ ...form, period: period as Form["period"] })}>
                <SelectTrigger className="w-full"><SelectValue /></SelectTrigger>
                <SelectContent>
                  <SelectItem value="monthly">Per month</SelectItem>
                  <SelectItem value="daily">Per day</SelectItem>
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-2">
              <Label htmlFor="b-limit">Limit (USD)</Label>
              <Input id="b-limit" type="number" min="0.01" step="0.01" required autoFocus={editing}
                value={form.limit_usd} onChange={(e) => setForm({ ...form, limit_usd: e.target.value })} />
            </div>
          </div>
          <DialogFooter><Button type="submit" disabled={!ready || save.isPending}>Save budget</Button></DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

export default function BudgetsPage() {
  const budgets = useQuery({ queryKey: ["budgets"], queryFn: () => get<BudgetStatus[]>("/admin/budgets"), refetchInterval: 30_000 });
  const alerts = useQuery({ queryKey: ["budget-alerts"], queryFn: () => get<BudgetAlert[]>("/admin/budget-alerts") });
  const remove = useAction((id: string) => del(`/admin/budgets/${id}`), { success: "Budget removed", invalidate: [["budgets"]] });
  return (
    <>
      <PageHeader title="Budgets"
        description="Spending limits for the whole company, a team or a person, on top of each key's own daily limit.">
        <BudgetDialog trigger={<Button><Plus /> Add budget</Button>} />
      </PageHeader>
      {budgets.isLoading ? <RowsSkeleton /> : !budgets.data?.length ? (
        <Empty icon={Wallet} title="No budgets yet">
          Set a monthly limit per team so one runaway app can&apos;t use up the AI bill.
        </Empty>
      ) : (
        <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
          {budgets.data.map((b) => (
            <Card key={b.id} className="gap-3">
              <CardHeader className="flex flex-row items-start justify-between gap-2">
                <div className="min-w-0 space-y-1">
                  <CardTitle className="truncate">{name(b)}</CardTitle>
                  <CardDescription>
                    {b.period === "monthly" ? "Per month" : "Per day"} · resets {resets(b.resets_at)}
                  </CardDescription>
                </div>
                <div className="flex shrink-0 items-center gap-1">
                  <Badge variant="outline" className="font-normal capitalize">{b.scope === "org" ? "company" : b.scope}</Badge>
                  <BudgetDialog
                    initial={{ scope: b.scope, target: b.target, period: b.period, limit_usd: String(b.limit_usd) }}
                    trigger={<Button variant="ghost" size="icon" className="size-8" aria-label={`Change ${name(b)} budget`}><Pencil /></Button>} />
                  <Button variant="ghost" size="icon" className="size-8 text-muted-foreground hover:text-destructive"
                    aria-label={`Remove ${name(b)} budget`} onClick={() => remove.mutate(b.id)}>
                    <Trash2 />
                  </Button>
                </div>
              </CardHeader>
              <CardContent className="space-y-2">
                <BudgetBar spent={b.spent_usd} limit={b.limit_usd} reached={b.limit_reached} />
                {b.reserved_usd > 0 && (
                  <p className="text-xs text-muted-foreground">{usd(b.reserved_usd)} held for requests in progress</p>
                )}
              </CardContent>
            </Card>
          ))}
        </div>
      )}
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2"><BellRing className="size-4" /> Alerts</CardTitle>
          <CardDescription>Sent once per period when a budget reaches 80% and 100%.</CardDescription>
        </CardHeader>
        <CardContent>
          {alerts.isLoading ? <RowsSkeleton rows={2} /> : !alerts.data?.length ? (
            <p className="text-sm text-muted-foreground">No alerts so far.</p>
          ) : (
            <ul className="divide-y">
              {alerts.data.map((a) => (
                <li key={a.id} className="flex items-center justify-between gap-4 py-2.5 text-sm">
                  <span className="min-w-0 truncate">
                    <Badge variant="outline" className={a.threshold_pct >= 100 ? "mr-2 border-destructive/40" : "mr-2 border-warning/40"}>
                      {a.threshold_pct}%
                    </Badge>
                    {a.budget} · {usd(a.spent_usd)} of {usd(a.limit_usd)}
                  </span>
                  <span className="shrink-0 font-mono text-xs text-muted-foreground">{ago(a.created_at)}</span>
                </li>
              ))}
            </ul>
          )}
        </CardContent>
      </Card>
    </>
  );
}
