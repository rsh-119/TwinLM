"use client";

import { useQuery } from "@tanstack/react-query";
import {
  ArrowLeft, KeyRound, MoreHorizontal, Plus, ShieldCheck, Trash2, UserMinus, UserPlus, KeySquare,
} from "lucide-react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";
import {
  BudgetBar, Empty, ExportButton, PageHeader, RoleBadge, RowsSkeleton, SecretReveal, Stat,
} from "@/components/common";
import { SpendChart } from "@/components/spend-chart";
import { Avatar, AvatarFallback } from "@/components/ui/avatar";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle, DialogTrigger,
} from "@/components/ui/dialog";
import {
  DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuSeparator, DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { del, get, patch, post } from "@/lib/api";
import { ago, initials, num, plural, usd } from "@/lib/format";
import { useAction, useMe, useTeams } from "@/lib/hooks";
import type { BudgetStatus, DailyPoint, MemberRow, TeamKey, TeamSummary, TeamUsage } from "@/lib/types";

const AVATAR_TONES = ["bg-orange-500", "bg-sky-500", "bg-emerald-500", "bg-violet-500", "bg-rose-500", "bg-amber-500", "bg-teal-500"];
const tone = (s: string) => AVATAR_TONES[[...s].reduce((a, c) => a + c.charCodeAt(0), 0) % AVATAR_TONES.length];

function AddMember({ team }: { team: TeamSummary }) {
  const [open, setOpen] = useState(false);
  const [form, setForm] = useState({ email: "", name: "", role: "member" });
  const [secret, setSecret] = useState<{ email: string; password?: string } | null>(null);
  const add = useAction(
    (body: typeof form) => post<{ email: string; temporary_password?: string }>(`/console/teams/${team.id}/members`,
      { ...body, name: body.name || null }),
    {
      invalidate: [["members", team.id], ["teams"]],
      onSuccess: (r) => setSecret({ email: r.email, password: r.temporary_password }),
    },
  );
  const close = (next: boolean) => {
    setOpen(next);
    if (!next) {
      setSecret(null);
      setForm({ email: "", name: "", role: "member" });
    }
  };
  return (
    <Dialog open={open} onOpenChange={close}>
      <DialogTrigger asChild>
        <Button><UserPlus /> Add member</Button>
      </DialogTrigger>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Add member to {team.name}</DialogTitle>
          <DialogDescription>New people get a temporary password to sign in with; they choose their own at first sign-in.</DialogDescription>
        </DialogHeader>
        {secret ? (
          <>
            {secret.password ? (
              <SecretReveal title={`Send ${secret.email} this temporary password`} value={secret.password}>
                Shown only now. TwinLM doesn&apos;t send email, so share it over chat.
              </SecretReveal>
            ) : (
              <p className="text-sm">{secret.email} already had an account and was added to the team.</p>
            )}
            <DialogFooter>
              <Button onClick={() => close(false)}>Done</Button>
            </DialogFooter>
          </>
        ) : (
          <form onSubmit={(e) => { e.preventDefault(); add.mutate(form); }} className="space-y-4">
            <div className="space-y-2">
              <Label htmlFor="m-email">Email</Label>
              <Input id="m-email" type="email" required autoFocus value={form.email}
                onChange={(e) => setForm({ ...form, email: e.target.value })} placeholder="name@company.com" />
            </div>
            <div className="grid gap-4 sm:grid-cols-[1fr_140px]">
              <div className="space-y-2">
                <Label htmlFor="m-name">Name</Label>
                <Input id="m-name" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })}
                  placeholder="For new people" />
              </div>
              <div className="space-y-2">
                <Label>Team role</Label>
                <Select value={form.role} onValueChange={(role) => setForm({ ...form, role })}>
                  <SelectTrigger className="w-full"><SelectValue /></SelectTrigger>
                  <SelectContent>
                    <SelectItem value="member">Member</SelectItem>
                    <SelectItem value="admin">Admin</SelectItem>
                  </SelectContent>
                </Select>
              </div>
            </div>
            <DialogFooter>
              <Button type="submit" disabled={add.isPending}>Add member</Button>
            </DialogFooter>
          </form>
        )}
      </DialogContent>
    </Dialog>
  );
}

function MemberActions({ team, member }: { team: TeamSummary; member: MemberRow }) {
  const [password, setPassword] = useState<string | null>(null);
  const keys = [["members", team.id], ["keys", team.id], ["teams"]];
  const role = useAction(
    (r: string) => patch(`/console/teams/${team.id}/members/${member.user_id}`, { role: r }),
    { success: "Role updated", invalidate: keys },
  );
  const reset = useAction(
    () => post<{ temporary_password: string }>(`/console/teams/${team.id}/members/${member.user_id}/reset-password`),
    { onSuccess: (r) => setPassword(r.temporary_password) },
  );
  const remove = useAction(
    () => del<{ revoked_keys: number }>(`/console/teams/${team.id}/members/${member.user_id}`),
    { success: (r) => `Removed. ${r.revoked_keys} of their keys revoked.`, invalidate: keys },
  );
  return (
    <>
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <Button variant="ghost" size="icon" className="size-8" aria-label={`Actions for ${member.name}`}>
            <MoreHorizontal />
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="end">
          <DropdownMenuItem onClick={() => role.mutate(member.role === "admin" ? "member" : "admin")}>
            <ShieldCheck /> {member.role === "admin" ? "Make member" : "Make team admin"}
          </DropdownMenuItem>
          <DropdownMenuItem onClick={() => reset.mutate()}>
            <KeySquare /> Reset password
          </DropdownMenuItem>
          <DropdownMenuSeparator />
          <DropdownMenuItem variant="destructive" onClick={() => remove.mutate()}>
            <UserMinus /> Remove from team
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>
      <Dialog open={!!password} onOpenChange={(o) => !o && setPassword(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Password reset</DialogTitle>
            <DialogDescription>{member.name} signs in with this and chooses a new password.</DialogDescription>
          </DialogHeader>
          {password && <SecretReveal title="Temporary password" value={password}>Shown only now.</SecretReveal>}
        </DialogContent>
      </Dialog>
    </>
  );
}

function Members({ team }: { team: TeamSummary }) {
  const members = useQuery({ queryKey: ["members", team.id], queryFn: () => get<MemberRow[]>(`/console/teams/${team.id}/members`) });
  const isAdmin = team.my_role === "admin";
  const rows = (members.data ?? []).map((m) => ({ member: m.name, email: m.email ?? "", role: m.role, last_active: m.last_active ?? "" }));
  return (
    <Card className="gap-0 py-0">
      <div className="flex flex-wrap items-center gap-3 border-b px-5 py-4">
        <div>
          <p className="font-medium">{team.name} — Members</p>
          <p className="text-sm text-muted-foreground">{plural(members.data?.filter((m) => m.type === "user").length, "person", "people")}</p>
        </div>
        <div className="ml-auto flex gap-2">
          <ExportButton filename={`${team.name}-members`} rows={rows} />
          {isAdmin && <AddMember team={team} />}
        </div>
      </div>
      {members.isLoading ? <div className="p-5"><RowsSkeleton /></div> : (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead className="pl-5">Member</TableHead>
              <TableHead>Team role</TableHead>
              <TableHead className="text-right">Last active</TableHead>
              {isAdmin && <TableHead className="w-12" />}
            </TableRow>
          </TableHeader>
          <TableBody>
            {members.data?.map((m) => (
              <TableRow key={m.user_id ?? m.key_id}>
                <TableCell className="pl-5">
                  <div className="flex items-center gap-3">
                    <Avatar className="size-8 rounded-md">
                      <AvatarFallback className={`rounded-md text-xs font-semibold text-white ${m.type === "service" ? "bg-zinc-500" : tone(m.name)}`}>
                        {initials(m.name)}
                      </AvatarFallback>
                    </Avatar>
                    <div className="min-w-0">
                      <p className="truncate font-medium">{m.name}</p>
                      <p className="truncate font-mono text-xs text-muted-foreground">{m.email ?? "service account"}</p>
                    </div>
                  </div>
                </TableCell>
                <TableCell><RoleBadge role={m.role} /></TableCell>
                <TableCell className="text-right font-mono text-xs text-muted-foreground">{ago(m.last_active)}</TableCell>
                {isAdmin && <TableCell>{m.type === "user" && <MemberActions team={team} member={m} />}</TableCell>}
              </TableRow>
            ))}
          </TableBody>
        </Table>
      )}
    </Card>
  );
}

function CreateKey({ team, members }: { team: TeamSummary; members: MemberRow[] }) {
  const { data: me } = useMe();
  const [open, setOpen] = useState(false);
  const [form, setForm] = useState({ name: "", owner: "me", rpm: "", budget: "" });
  const [raw, setRaw] = useState<string | null>(null);
  const create = useAction(
    () => post<TeamKey>(`/console/teams/${team.id}/keys`, {
      name: form.name || "key",
      for_user: form.owner === "service" ? null : form.owner,
      ...(form.rpm ? { rpm_limit: Number(form.rpm) } : {}),
      ...(form.budget ? { daily_budget_usd: Number(form.budget) } : {}),
    }),
    { invalidate: [["keys", team.id], ["members", team.id], ["teams"]], onSuccess: (k) => setRaw(k.key ?? null) },
  );
  const close = (next: boolean) => {
    setOpen(next);
    if (!next) {
      setRaw(null);
      setForm({ name: "", owner: "me", rpm: "", budget: "" });
    }
  };
  const owners = [
    { value: "me", label: "Me" },
    ...(team.my_role === "admin"
      ? [
          ...members.filter((m) => m.type === "user" && m.user_id !== me?.id).map((m) => ({ value: m.user_id!, label: m.name })),
          { value: "service", label: "Service (an application)" },
        ]
      : []),
  ];
  return (
    <Dialog open={open} onOpenChange={close}>
      <DialogTrigger asChild>
        <Button><Plus /> Create key</Button>
      </DialogTrigger>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Create a key</DialogTitle>
          <DialogDescription>Use it as the OpenAI SDK&apos;s api_key, with base_url pointing at TwinLM.</DialogDescription>
        </DialogHeader>
        {raw ? (
          <>
            <SecretReveal title="Your new key" value={raw}>Copy it now: it is shown only once.</SecretReveal>
            <DialogFooter><Button onClick={() => close(false)}>Done</Button></DialogFooter>
          </>
        ) : (
          <form onSubmit={(e) => { e.preventDefault(); create.mutate(undefined); }} className="space-y-4">
            <div className="grid gap-4 sm:grid-cols-2">
              <div className="space-y-2">
                <Label htmlFor="k-name">Name</Label>
                <Input id="k-name" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })}
                  placeholder="laptop or support-bot" autoFocus />
              </div>
              <div className="space-y-2">
                <Label>For</Label>
                <Select value={form.owner} onValueChange={(owner) => setForm({ ...form, owner })}>
                  <SelectTrigger className="w-full"><SelectValue /></SelectTrigger>
                  <SelectContent>
                    {owners.map((o) => <SelectItem key={o.value} value={o.value}>{o.label}</SelectItem>)}
                  </SelectContent>
                </Select>
              </div>
              <div className="space-y-2">
                <Label htmlFor="k-rpm">Requests per minute</Label>
                <Input id="k-rpm" type="number" min={1} value={form.rpm} onChange={(e) => setForm({ ...form, rpm: e.target.value })}
                  placeholder="No limit" />
              </div>
              <div className="space-y-2">
                <Label htmlFor="k-budget">Daily budget (USD)</Label>
                <Input id="k-budget" type="number" min={0} step="0.5" value={form.budget}
                  onChange={(e) => setForm({ ...form, budget: e.target.value })} placeholder="None" />
              </div>
            </div>
            <DialogFooter><Button type="submit" disabled={create.isPending}>Create key</Button></DialogFooter>
          </form>
        )}
      </DialogContent>
    </Dialog>
  );
}

function Keys({ team }: { team: TeamSummary }) {
  const keys = useQuery({ queryKey: ["keys", team.id], queryFn: () => get<TeamKey[]>(`/console/teams/${team.id}/keys`) });
  const members = useQuery({ queryKey: ["members", team.id], queryFn: () => get<MemberRow[]>(`/console/teams/${team.id}/members`) });
  const revoke = useAction((id: string) => del(`/console/teams/${team.id}/keys/${id}`), {
    success: "Key revoked",
    invalidate: [["keys", team.id], ["members", team.id], ["teams"]],
  });
  const rows = (keys.data ?? []).map((k) => ({
    name: k.name, owner: k.owner ?? "service", key: `${k.key_prefix}…`, rpm_limit: k.rpm_limit ?? "",
    daily_budget_usd: k.daily_budget_usd ?? "", last_used: k.last_used ?? "",
  }));
  return (
    <Card className="gap-0 py-0">
      <div className="flex flex-wrap items-center gap-3 border-b px-5 py-4">
        <div>
          <p className="font-medium">{team.my_role === "admin" ? "Team keys" : "Your keys"}</p>
          <p className="text-sm text-muted-foreground">Personal keys for people, service keys for applications.</p>
        </div>
        <div className="ml-auto flex gap-2">
          <ExportButton filename={`${team.name}-keys`} rows={rows} />
          <CreateKey team={team} members={members.data ?? []} />
        </div>
      </div>
      {keys.isLoading ? <div className="p-5"><RowsSkeleton /></div> : !keys.data?.length ? (
        <div className="p-5"><Empty icon={KeyRound} title="No keys yet">Create one to call TwinLM from your code.</Empty></div>
      ) : (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead className="pl-5">Name</TableHead>
              <TableHead>Owner</TableHead>
              <TableHead>Key</TableHead>
              <TableHead className="text-right">Limits</TableHead>
              <TableHead className="text-right">Last used</TableHead>
              <TableHead className="w-12" />
            </TableRow>
          </TableHeader>
          <TableBody>
            {keys.data.map((k) => (
              <TableRow key={k.id}>
                <TableCell className="pl-5 font-medium">{k.name}</TableCell>
                <TableCell>{k.owner ?? <RoleBadge role="service" />}</TableCell>
                <TableCell className="font-mono text-xs text-muted-foreground">{k.key_prefix}…</TableCell>
                <TableCell className="text-right text-xs text-muted-foreground">
                  {[k.rpm_limit && `${k.rpm_limit}/min`, k.daily_budget_usd != null && `${usd(k.daily_budget_usd)}/day`]
                    .filter(Boolean).join(" · ") || "None"}
                </TableCell>
                <TableCell className="text-right font-mono text-xs text-muted-foreground">{ago(k.last_used)}</TableCell>
                <TableCell>
                  <Button variant="ghost" size="icon" className="size-8 text-muted-foreground hover:text-destructive"
                    aria-label={`Revoke ${k.name}`} onClick={() => revoke.mutate(k.id)}>
                    <Trash2 />
                  </Button>
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      )}
    </Card>
  );
}

function Usage({ team }: { team: TeamSummary }) {
  const usage = useQuery({ queryKey: ["team-usage", team.id], queryFn: () => get<TeamUsage>(`/console/teams/${team.id}/usage`) });
  const daily = useQuery({
    queryKey: ["team-daily", team.id],
    queryFn: () => get<{ days: DailyPoint[] }>(`/console/teams/${team.id}/usage/daily`),
  });
  const budgets = useQuery({
    queryKey: ["team-budgets", team.id],
    queryFn: () => get<BudgetStatus[]>(`/console/teams/${team.id}/budgets`),
  });
  const t = usage.data?.total;
  return (
    <div className="space-y-4">
      {!!budgets.data?.length && (
        <div className="grid gap-4 sm:grid-cols-2">
          {budgets.data.map((b) => (
            <Card key={b.id} className="gap-3">
              <CardHeader>
                <CardTitle>{b.scope === "team" ? "Team budget" : "Your budget"}</CardTitle>
                <CardDescription>{b.period === "monthly" ? "Per month" : "Per day"}; requests stop at the limit</CardDescription>
              </CardHeader>
              <CardContent><BudgetBar spent={b.spent_usd} limit={b.limit_usd} reached={b.limit_reached} /></CardContent>
            </Card>
          ))}
        </div>
      )}
      <div className="grid gap-4 sm:grid-cols-3">
        <Stat label={usage.data?.scope === "mine" ? "Your requests" : "Requests"} value={num(t?.requests)} loading={usage.isLoading} />
        <Stat label="Spend" value={usd(t?.cost_usd ?? 0)} loading={usage.isLoading} hint="Last 30 days" />
        <Stat label="From cache" value={num(t?.cache_hits)} loading={usage.isLoading} hint="Served for $0" />
      </div>
      <Card>
        <CardHeader>
          <CardTitle>Spend per day</CardTitle>
          <CardDescription>{usage.data?.scope === "mine" ? "Your keys and playground use" : `All of ${team.name}`}</CardDescription>
        </CardHeader>
        <CardContent>{daily.data ? <SpendChart points={daily.data.days} /> : <RowsSkeleton rows={5} />}</CardContent>
      </Card>
    </div>
  );
}

function TeamView() {
  const params = useSearchParams();
  const id = params.get("id") ?? "";
  const tab = params.get("tab");
  const teams = useTeams();
  const team = teams.data?.find((t) => t.id === id);
  if (teams.isLoading) return <RowsSkeleton />;
  if (!team) return <Empty icon={ArrowLeft} title="Team not found"><Link className="text-brand" href="/teams/">Back to teams</Link></Empty>;
  return (
    <>
      <div>
        <Link href="/teams/" className="inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground">
          <ArrowLeft className="size-4" /> Teams
        </Link>
      </div>
      <PageHeader title={team.name} description={`${plural(team.members, "member")} · ${plural(team.active_keys, "active key")} · ${usd(team.spend_30d_usd)} in the last 30 days`}>
        <RoleBadge role={team.my_role} />
      </PageHeader>
      <Tabs defaultValue={tab === "keys" || tab === "usage" ? tab : "members"}>
        <TabsList>
          <TabsTrigger value="members">Members</TabsTrigger>
          <TabsTrigger value="keys">Keys</TabsTrigger>
          <TabsTrigger value="usage">Usage</TabsTrigger>
        </TabsList>
        <TabsContent value="members" className="mt-4"><Members team={team} /></TabsContent>
        <TabsContent value="keys" className="mt-4"><Keys team={team} /></TabsContent>
        <TabsContent value="usage" className="mt-4"><Usage team={team} /></TabsContent>
      </Tabs>
    </>
  );
}

export default function TeamViewPage() {
  return (
    <Suspense fallback={<RowsSkeleton />}>
      <TeamView />
    </Suspense>
  );
}
