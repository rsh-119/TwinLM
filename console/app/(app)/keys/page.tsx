"use client";

import { useQuery } from "@tanstack/react-query";
import { KeyRound, Trash2 } from "lucide-react";
import { useState } from "react";
import { Empty, ExportButton, PageHeader, RowsSkeleton } from "@/components/common";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { del, get } from "@/lib/api";
import { ago, usd } from "@/lib/format";
import { useAction } from "@/lib/hooks";
import type { OrgKey } from "@/lib/types";

export default function KeysPage() {
  const keys = useQuery({ queryKey: ["org-keys"], queryFn: () => get<OrgKey[]>("/admin/keys") });
  const [team, setTeam] = useState("all");
  const revoke = useAction((id: string) => del(`/admin/keys/${id}`), { success: "Key revoked", invalidate: [["org-keys"], ["teams"]] });
  const active = (keys.data ?? []).filter((k) => !k.revoked_at);
  const teams = [...new Set(active.map((k) => k.project))].sort();
  const shown = team === "all" ? active : active.filter((k) => k.project === team);
  const rows = shown.map((k) => ({
    name: k.name, team: k.project, key: `${k.key_prefix}…`, kind: k.user_id ? "personal" : "service",
    models: (k.allowed_models ?? []).join("; ") || "all", rpm_limit: k.rpm_limit ?? "", daily_budget_usd: k.daily_budget_usd ?? "",
    created_at: k.created_at ?? "",
  }));
  return (
    <>
      <PageHeader title="Keys" description="Every active API key across teams. Teams create their own keys on their team page.">
        <Select value={team} onValueChange={setTeam}>
          <SelectTrigger className="w-44"><SelectValue /></SelectTrigger>
          <SelectContent>
            <SelectItem value="all">All teams</SelectItem>
            {teams.map((t) => <SelectItem key={t} value={t}>{t}</SelectItem>)}
          </SelectContent>
        </Select>
        <ExportButton filename="keys" rows={rows} />
      </PageHeader>
      {keys.isLoading ? <RowsSkeleton /> : !shown.length ? (
        <Empty icon={KeyRound} title="No active keys" />
      ) : (
        <Card className="py-0">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead className="pl-5">Name</TableHead>
                <TableHead>Team</TableHead>
                <TableHead>Type</TableHead>
                <TableHead>Key</TableHead>
                <TableHead>Models</TableHead>
                <TableHead className="text-right">Limits</TableHead>
                <TableHead className="text-right">Created</TableHead>
                <TableHead className="w-12" />
              </TableRow>
            </TableHeader>
            <TableBody>
              {shown.map((k) => (
                <TableRow key={k.id}>
                  <TableCell className="pl-5 font-medium">{k.name}</TableCell>
                  <TableCell>{k.project}</TableCell>
                  <TableCell>
                    <Badge variant="outline" className="font-normal">{k.user_id ? "Personal" : "Service"}</Badge>
                  </TableCell>
                  <TableCell className="font-mono text-xs text-muted-foreground">{k.key_prefix}…</TableCell>
                  <TableCell className="max-w-40 truncate text-sm">{(k.allowed_models ?? []).join(", ") || "All"}</TableCell>
                  <TableCell className="text-right text-xs text-muted-foreground">
                    {[k.rpm_limit && `${k.rpm_limit}/min`, k.daily_budget_usd != null && `${usd(k.daily_budget_usd)}/day`]
                      .filter(Boolean).join(" · ") || "None"}
                  </TableCell>
                  <TableCell className="text-right font-mono text-xs text-muted-foreground">{ago(k.created_at)}</TableCell>
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
        </Card>
      )}
    </>
  );
}
