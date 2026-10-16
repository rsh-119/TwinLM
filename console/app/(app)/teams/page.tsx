"use client";

import { ArrowRight, Plus, Users } from "lucide-react";
import Link from "next/link";
import { useState } from "react";
import { Empty, ExportButton, PageHeader, RoleBadge, RowsSkeleton } from "@/components/common";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import {
  Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle, DialogTrigger,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { post } from "@/lib/api";
import { num, usd } from "@/lib/format";
import { useAction, useMe, useTeams } from "@/lib/hooks";

function CreateTeam() {
  const [open, setOpen] = useState(false);
  const [name, setName] = useState("");
  const create = useAction((n: string) => post("/admin/teams", { name: n }), {
    success: "Team created",
    invalidate: [["teams"]],
    onSuccess: () => {
      setOpen(false);
      setName("");
    },
  });
  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button><Plus /> New team</Button>
      </DialogTrigger>
      <DialogContent>
        <form onSubmit={(e) => { e.preventDefault(); create.mutate(name); }} className="space-y-4">
          <DialogHeader>
            <DialogTitle>New team</DialogTitle>
            <DialogDescription>Teams own keys and see their own spend. The name is used as the project of their keys.</DialogDescription>
          </DialogHeader>
          <div className="space-y-2">
            <Label htmlFor="team-name">Name</Label>
            <Input id="team-name" value={name} onChange={(e) => setName(e.target.value.toLowerCase())}
              placeholder="platform-eng" pattern="[a-z0-9][a-z0-9_.\-]{0,39}" required autoFocus />
            <p className="text-xs text-muted-foreground">Lowercase letters, digits, dashes, dots or underscores.</p>
          </div>
          <DialogFooter>
            <Button type="submit" disabled={create.isPending || !name}>Create team</Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

export default function TeamsPage() {
  const { data: me } = useMe();
  const teams = useTeams();
  const rows = (teams.data ?? []).map((t) => ({
    team: t.name, your_role: t.my_role, members: t.members, active_keys: t.active_keys, spend_30d_usd: t.spend_30d_usd,
  }));
  return (
    <>
      <PageHeader title="Teams" description={me?.role === "admin" ? "Every team in your company." : "The teams you belong to."}>
        <ExportButton filename="teams" rows={rows} />
        {me?.role === "admin" && <CreateTeam />}
      </PageHeader>
      {teams.isLoading ? <RowsSkeleton /> : !teams.data?.length ? (
        <Empty icon={Users} title={me?.role === "admin" ? "No teams yet" : "You're not in a team yet"}>
          {me?.role === "admin" ? "Create a team, then add people and keys to it." : "Ask an admin to add you to a team."}
        </Empty>
      ) : (
        <Card className="py-0">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead className="pl-5">Team</TableHead>
                <TableHead>Your role</TableHead>
                <TableHead className="text-right">Members</TableHead>
                <TableHead className="text-right">Active keys</TableHead>
                <TableHead className="text-right">Spend, 30 days</TableHead>
                <TableHead className="w-10" />
              </TableRow>
            </TableHeader>
            <TableBody>
              {teams.data.map((t) => (
                <TableRow key={t.id} className="group">
                  <TableCell className="pl-5 font-medium">
                    <Link href={`/teams/view/?id=${t.id}`} className="hover:text-brand">{t.name}</Link>
                  </TableCell>
                  <TableCell><RoleBadge role={t.my_role} /></TableCell>
                  <TableCell className="tabular text-right">{num(t.members)}</TableCell>
                  <TableCell className="tabular text-right">{num(t.active_keys)}</TableCell>
                  <TableCell className="tabular text-right">{usd(t.spend_30d_usd)}</TableCell>
                  <TableCell>
                    <Button asChild variant="ghost" size="icon" className="size-8">
                      <Link href={`/teams/view/?id=${t.id}`} aria-label={`Open ${t.name}`}><ArrowRight /></Link>
                    </Button>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </Card>
      )}
    </>
  );
}
