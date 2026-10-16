"use client";

import { useQuery } from "@tanstack/react-query";
import { Ban, CircleCheck, KeySquare, MoreHorizontal, ShieldCheck, UserPlus } from "lucide-react";
import { useState } from "react";
import { ExportButton, PageHeader, RoleBadge, RowsSkeleton, SecretReveal } from "@/components/common";
import { Avatar, AvatarFallback } from "@/components/ui/avatar";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
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
import { get, patch, post } from "@/lib/api";
import { ago, initials } from "@/lib/format";
import { useAction } from "@/lib/hooks";
import type { Person } from "@/lib/types";

function status(p: Person) {
  if (p.disabled) return <Badge variant="outline" className="font-normal text-muted-foreground">Disabled</Badge>;
  if (p.must_change_password) return <Badge variant="outline" className="border-warning/40 font-normal">Invited</Badge>;
  return <Badge variant="outline" className="border-success/40 font-normal">Active</Badge>;
}

function AddPerson() {
  const [open, setOpen] = useState(false);
  const [form, setForm] = useState({ email: "", name: "", role: "member" });
  const [password, setPassword] = useState<string | null>(null);
  const add = useAction((body: typeof form) => post<{ temporary_password: string }>("/admin/users", body), {
    invalidate: [["people"]],
    onSuccess: (r) => setPassword(r.temporary_password),
  });
  const close = (next: boolean) => {
    setOpen(next);
    if (!next) {
      setPassword(null);
      setForm({ email: "", name: "", role: "member" });
    }
  };
  return (
    <Dialog open={open} onOpenChange={close}>
      <DialogTrigger asChild>
        <Button><UserPlus /> Add person</Button>
      </DialogTrigger>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Add a person</DialogTitle>
          <DialogDescription>Add them to teams afterwards from the team&apos;s page.</DialogDescription>
        </DialogHeader>
        {password ? (
          <>
            <SecretReveal title={`Send ${form.email} this temporary password`} value={password}>Shown only now.</SecretReveal>
            <DialogFooter><Button onClick={() => close(false)}>Done</Button></DialogFooter>
          </>
        ) : (
          <form onSubmit={(e) => { e.preventDefault(); add.mutate({ ...form, name: form.name || form.email }); }} className="space-y-4">
            <div className="space-y-2">
              <Label htmlFor="p-email">Email</Label>
              <Input id="p-email" type="email" required autoFocus value={form.email}
                onChange={(e) => setForm({ ...form, email: e.target.value })} />
            </div>
            <div className="grid gap-4 sm:grid-cols-[1fr_140px]">
              <div className="space-y-2">
                <Label htmlFor="p-name">Name</Label>
                <Input id="p-name" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} />
              </div>
              <div className="space-y-2">
                <Label>Role</Label>
                <Select value={form.role} onValueChange={(role) => setForm({ ...form, role })}>
                  <SelectTrigger className="w-full"><SelectValue /></SelectTrigger>
                  <SelectContent>
                    <SelectItem value="member">Member</SelectItem>
                    <SelectItem value="admin">Admin</SelectItem>
                  </SelectContent>
                </Select>
              </div>
            </div>
            <DialogFooter><Button type="submit" disabled={add.isPending}>Add person</Button></DialogFooter>
          </form>
        )}
      </DialogContent>
    </Dialog>
  );
}

function PersonActions({ person }: { person: Person }) {
  const [password, setPassword] = useState<string | null>(null);
  const update = useAction((body: Record<string, unknown>) => patch<{ temporary_password?: string }>(`/admin/users/${person.id}`, body), {
    invalidate: [["people"]],
    onSuccess: (r) => (r.temporary_password ? setPassword(r.temporary_password) : undefined),
    success: (r) => (r.temporary_password ? "Password reset" : "Saved"),
  });
  return (
    <>
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <Button variant="ghost" size="icon" className="size-8" aria-label={`Actions for ${person.name}`}><MoreHorizontal /></Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="end">
          <DropdownMenuItem onClick={() => update.mutate({ role: person.role === "admin" ? "member" : "admin" })}>
            <ShieldCheck /> {person.role === "admin" ? "Make member" : "Make admin"}
          </DropdownMenuItem>
          <DropdownMenuItem onClick={() => update.mutate({ reset_password: true })}>
            <KeySquare /> Reset password
          </DropdownMenuItem>
          <DropdownMenuSeparator />
          {person.disabled ? (
            <DropdownMenuItem onClick={() => update.mutate({ disabled: false })}><CircleCheck /> Enable</DropdownMenuItem>
          ) : (
            <DropdownMenuItem variant="destructive" onClick={() => update.mutate({ disabled: true })}><Ban /> Disable</DropdownMenuItem>
          )}
        </DropdownMenuContent>
      </DropdownMenu>
      <Dialog open={!!password} onOpenChange={(o) => !o && setPassword(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Password reset</DialogTitle>
            <DialogDescription>{person.name} signs in with this and chooses a new password.</DialogDescription>
          </DialogHeader>
          {password && <SecretReveal title="Temporary password" value={password}>Shown only now.</SecretReveal>}
        </DialogContent>
      </Dialog>
    </>
  );
}

export default function PeoplePage() {
  const people = useQuery({ queryKey: ["people"], queryFn: () => get<Person[]>("/admin/users") });
  const rows = (people.data ?? []).map((p) => ({
    name: p.name, email: p.email, role: p.role, teams: p.teams.map((t) => t.name).join("; "),
    last_sign_in: p.last_login_at ?? "", status: p.disabled ? "disabled" : p.must_change_password ? "invited" : "active",
  }));
  return (
    <>
      <PageHeader title="People" description="Everyone who can sign in to TwinLM.">
        <ExportButton filename="people" rows={rows} />
        <AddPerson />
      </PageHeader>
      {people.isLoading ? <RowsSkeleton /> : (
        <Card className="py-0">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead className="pl-5">Person</TableHead>
                <TableHead>Role</TableHead>
                <TableHead>Teams</TableHead>
                <TableHead>Status</TableHead>
                <TableHead className="text-right">Last sign-in</TableHead>
                <TableHead className="w-12" />
              </TableRow>
            </TableHeader>
            <TableBody>
              {people.data?.map((p) => (
                <TableRow key={p.id}>
                  <TableCell className="pl-5">
                    <div className="flex items-center gap-3">
                      <Avatar className="size-8 rounded-md"><AvatarFallback className="rounded-md text-xs">{initials(p.name)}</AvatarFallback></Avatar>
                      <div className="min-w-0">
                        <p className="truncate font-medium">{p.name}</p>
                        <p className="truncate font-mono text-xs text-muted-foreground">{p.email}</p>
                      </div>
                    </div>
                  </TableCell>
                  <TableCell><RoleBadge role={p.role} /></TableCell>
                  <TableCell className="max-w-56 truncate text-sm text-muted-foreground">
                    {p.teams.map((t) => t.name).join(", ") || "—"}
                  </TableCell>
                  <TableCell>{status(p)}</TableCell>
                  <TableCell className="text-right font-mono text-xs text-muted-foreground">{ago(p.last_login_at)}</TableCell>
                  <TableCell><PersonActions person={p} /></TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </Card>
      )}
    </>
  );
}
