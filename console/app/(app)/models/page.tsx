"use client";

import { useQuery } from "@tanstack/react-query";
import { ArrowDown, ArrowUp, Pencil, Plus, RotateCcw, Trash2, X } from "lucide-react";
import { useState } from "react";
import { ExportButton, PageHeader, RowsSkeleton } from "@/components/common";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle, DialogTrigger,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Sheet, SheetContent, SheetDescription, SheetFooter, SheetHeader, SheetTitle } from "@/components/ui/sheet";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { del, get, put } from "@/lib/api";
import { useAction } from "@/lib/hooks";
import type { AliasRow, Preset, PriceRow, ProviderRow } from "@/lib/types";

function useTargetOptions() {
  const providers = useQuery({ queryKey: ["providers"], queryFn: () => get<ProviderRow[]>("/admin/config/providers") });
  const presets = useQuery({ queryKey: ["presets"], queryFn: () => get<Record<string, Preset>>("/admin/config/presets"), staleTime: Infinity });
  const out: string[] = [];
  for (const p of providers.data ?? []) {
    if (!p.enabled) continue;
    out.push(`${p.name}/${p.default_model}`);
    for (const m of presets.data?.[p.preset ?? ""]?.models ?? []) out.push(`${p.name}/${m}`);
  }
  return [...new Set(out)];
}

function EditModel({ alias, open, onOpenChange }: { alias: AliasRow | null; open: boolean; onOpenChange: (o: boolean) => void }) {
  const options = useTargetOptions();
  const [name, setName] = useState(alias?.alias ?? "");
  const [targets, setTargets] = useState<string[]>(alias?.targets ?? []);
  const [next, setNext] = useState("");
  const [cap, setCap] = useState(alias?.max_output_tokens?.toString() ?? "");
  const [ttl, setTtl] = useState(alias?.cache_ttl_seconds?.toString() ?? "");
  const save = useAction(
    () => put(`/admin/config/aliases/${name}`, {
      targets, max_output_tokens: cap ? Number(cap) : null, cache_ttl_seconds: ttl === "" ? null : Number(ttl),
    }),
    { success: `${name} saved`, invalidate: [["aliases"]], onSuccess: () => onOpenChange(false) },
  );
  const move = (i: number, d: number) => {
    const copy = [...targets];
    [copy[i], copy[i + d]] = [copy[i + d], copy[i]];
    setTargets(copy);
  };
  const add = () => {
    if (next && !targets.includes(next)) setTargets([...targets, next]);
    setNext("");
  };
  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent className="w-full overflow-y-auto sm:max-w-lg">
        <SheetHeader>
          <SheetTitle>{alias ? `Edit ${alias.alias}` : "New model"}</SheetTitle>
          <SheetDescription>Apps send this name as <code>model</code>. TwinLM tries the targets in order; the rest are fallbacks.</SheetDescription>
        </SheetHeader>
        <form className="space-y-5 px-4" onSubmit={(e) => { e.preventDefault(); save.mutate(undefined); }}>
          {!alias && (
            <div className="space-y-2">
              <Label htmlFor="md-name">Name</Label>
              <Input id="md-name" required value={name} onChange={(e) => setName(e.target.value.toLowerCase())} placeholder="support" />
            </div>
          )}
          <div className="space-y-2">
            <Label>Try in this order</Label>
            <ol className="space-y-1.5">
              {targets.map((t, i) => (
                <li key={t} className="flex items-center gap-2 rounded-md border px-2 py-1.5">
                  <span className="tabular w-5 text-center text-xs text-muted-foreground">{i + 1}</span>
                  <span className="min-w-0 flex-1 truncate font-mono text-xs">{t}</span>
                  <Button type="button" variant="ghost" size="icon" className="size-7" disabled={i === 0} onClick={() => move(i, -1)} aria-label="Move up"><ArrowUp /></Button>
                  <Button type="button" variant="ghost" size="icon" className="size-7" disabled={i === targets.length - 1} onClick={() => move(i, 1)} aria-label="Move down"><ArrowDown /></Button>
                  <Button type="button" variant="ghost" size="icon" className="size-7" onClick={() => setTargets(targets.filter((x) => x !== t))} aria-label="Remove"><X /></Button>
                </li>
              ))}
              {!targets.length && <li className="text-sm text-muted-foreground">Add at least one provider/model.</li>}
            </ol>
            <div className="flex gap-2">
              <Input list="md-options" value={next} onChange={(e) => setNext(e.target.value)} placeholder="provider/model"
                className="font-mono text-xs" onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); add(); } }} />
              <datalist id="md-options">{options.filter((o) => !targets.includes(o)).map((o) => <option key={o} value={o} />)}</datalist>
              <Button type="button" variant="outline" onClick={add} disabled={!next}><Plus /> Add</Button>
            </div>
          </div>
          <div className="grid gap-4 sm:grid-cols-2">
            <div className="space-y-2">
              <Label htmlFor="md-cap">Output cap (tokens)</Label>
              <Input id="md-cap" type="number" min={1} value={cap} onChange={(e) => setCap(e.target.value)} placeholder="Gateway default" />
            </div>
            <div className="space-y-2">
              <Label htmlFor="md-ttl">Cache TTL (seconds)</Label>
              <Input id="md-ttl" type="number" min={0} value={ttl} onChange={(e) => setTtl(e.target.value)} placeholder="Gateway default" />
              <p className="text-xs text-muted-foreground">0 = never cache this model.</p>
            </div>
          </div>
          <SheetFooter className="px-0">
            <Button type="submit" disabled={save.isPending || !targets.length || !name}>Save model</Button>
          </SheetFooter>
        </form>
      </SheetContent>
    </Sheet>
  );
}

function PriceDialog({ row }: { row?: PriceRow }) {
  const [open, setOpen] = useState(false);
  const [form, setForm] = useState({ key: row?.key ?? "", input: row?.input_per_million.toString() ?? "", output: row?.output_per_million.toString() ?? "" });
  const options = useTargetOptions();
  const save = useAction(
    () => put("/admin/config/prices", { key: form.key, input_per_million: Number(form.input), output_per_million: Number(form.output) }),
    { success: "Price saved", invalidate: [["prices"]], onSuccess: () => setOpen(false) },
  );
  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        {row ? (
          <Button variant="ghost" size="icon" className="size-8" aria-label={`Edit price of ${row.key}`}><Pencil /></Button>
        ) : (
          <Button variant="outline" size="sm"><Plus /> Add price</Button>
        )}
      </DialogTrigger>
      <DialogContent>
        <form onSubmit={(e) => { e.preventDefault(); save.mutate(undefined); }} className="space-y-4">
          <DialogHeader>
            <DialogTitle>{row ? `Price of ${row.key}` : "Add a price"}</DialogTitle>
            <DialogDescription>USD per million tokens. Check your provider&apos;s current price list.</DialogDescription>
          </DialogHeader>
          {!row && (
            <div className="space-y-2">
              <Label htmlFor="pr-key">provider/model</Label>
              <Input id="pr-key" list="pr-options" required value={form.key} onChange={(e) => setForm({ ...form, key: e.target.value })} className="font-mono text-xs" />
              <datalist id="pr-options">{options.map((o) => <option key={o} value={o} />)}</datalist>
            </div>
          )}
          <div className="grid grid-cols-2 gap-4">
            <div className="space-y-2">
              <Label htmlFor="pr-in">Input $</Label>
              <Input id="pr-in" type="number" step="0.001" min={0} required value={form.input} onChange={(e) => setForm({ ...form, input: e.target.value })} />
            </div>
            <div className="space-y-2">
              <Label htmlFor="pr-out">Output $</Label>
              <Input id="pr-out" type="number" step="0.001" min={0} required value={form.output} onChange={(e) => setForm({ ...form, output: e.target.value })} />
            </div>
          </div>
          <DialogFooter><Button type="submit" disabled={save.isPending}>Save price</Button></DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

export default function ModelsPage() {
  const aliases = useQuery({ queryKey: ["aliases"], queryFn: () => get<AliasRow[]>("/admin/config/aliases") });
  const prices = useQuery({ queryKey: ["prices"], queryFn: () => get<PriceRow[]>("/admin/config/prices") });
  const [editing, setEditing] = useState<AliasRow | null | undefined>(undefined);
  const remove = useAction((alias: string) => del(`/admin/config/aliases/${alias}`), { success: "Done", invalidate: [["aliases"]] });
  const removePrice = useAction((key: string) => del("/admin/config/prices", { key }), { success: "Price removed", invalidate: [["prices"]] });
  const priceOf = new Map((prices.data ?? []).map((p) => [p.key, p]));
  return (
    <>
      <PageHeader title="Models" description="The names your apps use as model, what they try in order, and what they cost.">
        <Button onClick={() => setEditing(null)}><Plus /> New model</Button>
      </PageHeader>
      <Card className="py-0">
        {aliases.isLoading ? <div className="p-5"><RowsSkeleton /></div> : (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead className="pl-5">Model</TableHead>
                <TableHead>Tries, in order</TableHead>
                <TableHead className="text-right">Output cap</TableHead>
                <TableHead className="text-right">Cache TTL</TableHead>
                <TableHead>Set in</TableHead>
                <TableHead className="w-24" />
              </TableRow>
            </TableHeader>
            <TableBody>
              {aliases.data?.map((a) => (
                <TableRow key={a.alias}>
                  <TableCell className="pl-5 font-mono font-medium">{a.alias}</TableCell>
                  <TableCell>
                    <div className="flex flex-wrap items-center gap-1">
                      {a.targets.map((t, i) => (
                        <span key={t} className="flex items-center gap-1">
                          {i > 0 && <span className="text-muted-foreground">→</span>}
                          <Badge variant={i === 0 ? "secondary" : "outline"} className="font-mono font-normal"
                            title={priceOf.get(t) ? `$${priceOf.get(t)!.input_per_million}/$${priceOf.get(t)!.output_per_million} per 1M` : "no price"}>
                            {t}
                          </Badge>
                        </span>
                      ))}
                    </div>
                  </TableCell>
                  <TableCell className="tabular text-right">{a.max_output_tokens ?? "default"}</TableCell>
                  <TableCell className="tabular text-right">{a.cache_ttl_seconds ?? "default"}</TableCell>
                  <TableCell><Badge variant="outline" className="font-normal">{a.source === "panel" ? "Console" : "gateway.toml"}</Badge></TableCell>
                  <TableCell>
                    <div className="flex justify-end">
                      <Button variant="ghost" size="icon" className="size-8" onClick={() => setEditing(a)} aria-label={`Edit ${a.alias}`}><Pencil /></Button>
                      {a.source === "panel" && (
                        <Button variant="ghost" size="icon" className="size-8 text-muted-foreground hover:text-destructive"
                          title={a.overrides_toml ? "Reset to gateway.toml" : "Delete"} onClick={() => remove.mutate(a.alias)}
                          aria-label={a.overrides_toml ? `Reset ${a.alias}` : `Delete ${a.alias}`}>
                          {a.overrides_toml ? <RotateCcw /> : <Trash2 />}
                        </Button>
                      )}
                    </div>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
      </Card>

      <Card>
        <CardHeader className="flex flex-row items-start justify-between gap-4">
          <div>
            <CardTitle>Prices</CardTitle>
            <CardDescription>USD per million tokens. Without a price, a model&apos;s cost shows as unknown.</CardDescription>
          </div>
          <div className="flex gap-2">
            <ExportButton filename="prices" rows={(prices.data ?? []).map((p) => ({ ...p }))} />
            <PriceDialog />
          </div>
        </CardHeader>
        <CardContent className="px-0">
          {prices.isLoading ? <div className="px-5"><RowsSkeleton /></div> : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead className="pl-5">provider/model</TableHead>
                  <TableHead className="text-right">Input</TableHead>
                  <TableHead className="text-right">Output</TableHead>
                  <TableHead>Set in</TableHead>
                  <TableHead className="w-24" />
                </TableRow>
              </TableHeader>
              <TableBody>
                {prices.data?.map((p) => (
                  <TableRow key={p.key}>
                    <TableCell className="pl-5 font-mono text-xs">{p.key}</TableCell>
                    <TableCell className="tabular text-right">${p.input_per_million}</TableCell>
                    <TableCell className="tabular text-right">${p.output_per_million}</TableCell>
                    <TableCell><Badge variant="outline" className="font-normal">{p.source === "panel" ? "Console" : "gateway.toml"}</Badge></TableCell>
                    <TableCell>
                      <div className="flex justify-end">
                        <PriceDialog row={p} />
                        {p.source === "panel" && (
                          <Button variant="ghost" size="icon" className="size-8 text-muted-foreground hover:text-destructive"
                            onClick={() => removePrice.mutate(p.key)} aria-label={`Remove price of ${p.key}`}><Trash2 /></Button>
                        )}
                      </div>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>
      {editing !== undefined && (
        <EditModel key={editing?.alias ?? "new"} alias={editing} open onOpenChange={(o) => !o && setEditing(undefined)} />
      )}
    </>
  );
}
