"use client";

import { useQuery } from "@tanstack/react-query";
import {
  Boxes, CheckCircle2, ExternalLink, KeySquare, Loader2, MoreHorizontal, Plug, Plus, Power, Trash2, XCircle, Zap,
} from "lucide-react";
import { useState } from "react";
import { PageHeader, RowsSkeleton, StatusDot } from "@/components/common";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle,
} from "@/components/ui/dialog";
import {
  DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuSeparator, DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select, SelectContent, SelectGroup, SelectItem, SelectLabel, SelectSeparator, SelectTrigger, SelectValue,
} from "@/components/ui/select";
import { Sheet, SheetContent, SheetDescription, SheetFooter, SheetHeader, SheetTitle, SheetTrigger } from "@/components/ui/sheet";
import { del, get, patch, post } from "@/lib/api";
import { useAction } from "@/lib/hooks";
import { cn } from "@/lib/utils";
import type { Preset, ProviderRow, TestResult } from "@/lib/types";

const presetsQuery = { queryKey: ["presets"], queryFn: () => get<Record<string, Preset>>("/admin/config/presets"), staleTime: Infinity };

function TestOutcome({ result }: { result: TestResult | null }) {
  if (!result) return null;
  return result.ok ? (
    <div className="flex items-start gap-2 rounded-md border border-success/30 bg-success/5 p-3 text-sm">
      <CheckCircle2 className="mt-0.5 size-4 text-success" />
      <div>
        <p className="font-medium">Connected — {result.model} answered in {result.latency_ms} ms.</p>
        {!!result.models?.length && <p className="text-muted-foreground">{result.models.length} models available on this account.</p>}
      </div>
    </div>
  ) : (
    <div className="flex items-start gap-2 rounded-md border border-destructive/30 bg-destructive/5 p-3 text-sm">
      <XCircle className="mt-0.5 size-4 text-destructive" />
      <p className="min-w-0 break-words">{result.error}</p>
    </div>
  );
}

const OTHER = "__other__";

/** Model dropdown: the preset's suggestions (lightest first) and, once a test
 * has run, every chat model the key can use. "Other" allows any name. */
function ModelPicker({ id, value, onChange, suggested, live }: {
  id: string;
  value: string;
  onChange: (model: string) => void;
  suggested: string[];
  live?: string[];
}) {
  const known = live?.length ? live : suggested;
  const [custom, setCustom] = useState(!!value && !known.includes(value));
  const recommended = live?.length ? suggested.filter((m) => live.includes(m)) : suggested;
  const rest = (live ?? []).filter((m) => !recommended.includes(m));
  if (custom || !known.length) {
    return (
      <div className="flex gap-2">
        <Input id={id} required value={value} onChange={(e) => onChange(e.target.value)} placeholder="model name"
          className="font-mono text-xs" />
        {!!known.length && (
          <Button type="button" variant="ghost" size="sm" onClick={() => { setCustom(false); onChange(recommended[0] ?? known[0]); }}>
            List
          </Button>
        )}
      </div>
    );
  }
  return (
    <Select value={value || undefined} onValueChange={(m) => (m === OTHER ? setCustom(true) : onChange(m))}>
      <SelectTrigger id={id} className="w-full font-mono text-xs"><SelectValue placeholder="Choose a model" /></SelectTrigger>
      <SelectContent className="max-h-80">
        {!!recommended.length && (
          <SelectGroup>
            <SelectLabel>Recommended, lightest first</SelectLabel>
            {recommended.map((m, i) => (
              <SelectItem key={m} value={m} className="font-mono text-xs">
                {m}{i === 0 && <span className="ml-1 font-sans text-muted-foreground">· lightest</span>}
              </SelectItem>
            ))}
          </SelectGroup>
        )}
        {!!rest.length && (
          <SelectGroup>
            <SelectSeparator />
            <SelectLabel>All models on this key</SelectLabel>
            {rest.map((m) => <SelectItem key={m} value={m} className="font-mono text-xs">{m}</SelectItem>)}
          </SelectGroup>
        )}
        <SelectSeparator />
        <SelectItem value={OTHER}>Other model…</SelectItem>
      </SelectContent>
    </Select>
  );
}

/** After a test: if the chosen model isn't available on this key, switch to
 * the lightest one that is. Returns the note to show, if it switched. */
function adoptRecommended(result: TestResult, model: string, setModel: (m: string) => void): string | null {
  if (!result.models?.length || result.models.includes(model) || !result.recommended) return null;
  setModel(result.recommended);
  return `${model} isn't available on this key, so ${result.recommended} is selected. Test again to confirm.`;
}

function AddProvider({ existing }: { existing: string[] }) {
  const presets = useQuery(presetsQuery);
  const [open, setOpen] = useState(false);
  const [presetId, setPresetId] = useState<string | null>(null);
  const [form, setForm] = useState({ name: "", model: "", base_url: "", api_key: "" });
  const [test, setTest] = useState<TestResult | null>(null);
  const [testing, setTesting] = useState(false);
  const [switched, setSwitched] = useState<string | null>(null);
  const preset = presetId ? presets.data?.[presetId] : undefined;

  const pick = (id: string) => {
    const p = presets.data![id];
    setPresetId(id);
    setTest(null);
    setSwitched(null);
    setForm({ name: existing.includes(id) ? `${id}-2` : id, model: p.default_model, base_url: p.base_url ?? "", api_key: "" });
  };
  const reset = (next: boolean) => {
    setOpen(next);
    if (!next) {
      setPresetId(null);
      setTest(null);
    }
  };
  const runTest = async () => {
    setTesting(true);
    try {
      const result = await post<TestResult>("/admin/config/test", {
        preset: presetId, base_url: form.base_url || null, api_key: form.api_key || null, model: form.model,
      });
      setTest(result);
      setSwitched(adoptRecommended(result, form.model, (model) => setForm((f) => ({ ...f, model }))));
    } catch (e) {
      setTest({ ok: false, model: form.model, latency_ms: 0, error: (e as Error).message });
    } finally {
      setTesting(false);
    }
  };
  const save = useAction(
    () => post("/admin/config/providers", {
      name: form.name, preset: presetId, default_model: form.model, base_url: form.base_url || null, api_key: form.api_key || null,
    }),
    { success: `${form.name} added — use it on the Models page`, invalidate: [["providers"], ["aliases"]], onSuccess: () => reset(false) },
  );

  return (
    <Sheet open={open} onOpenChange={reset}>
      <SheetTrigger asChild>
        <Button><Plus /> Add provider</Button>
      </SheetTrigger>
      <SheetContent className="w-full overflow-y-auto sm:max-w-lg">
        <SheetHeader>
          <SheetTitle>{preset ? `Add ${preset.label}` : "Add a provider"}</SheetTitle>
          <SheetDescription>
            {preset ? "Paste the API key, test it, and save. It works immediately." : "Pick the provider you want to connect."}
          </SheetDescription>
        </SheetHeader>
        {!preset ? (
          <div className="grid grid-cols-2 gap-2 px-4">
            {presets.isLoading && <RowsSkeleton />}
            {Object.entries(presets.data ?? {}).map(([id, p]) => (
              <button key={id} type="button" onClick={() => pick(id)}
                className="rounded-lg border p-3 text-left transition-colors hover:border-brand hover:bg-brand/5">
                <p className="flex items-center gap-1.5 text-sm font-medium">
                  {p.label}
                  {p.free_tier && <Badge variant="outline" className="border-success/40 px-1.5 py-0 text-[10px] font-normal">Free tier</Badge>}
                </p>
                <p className="mt-0.5 truncate text-xs text-muted-foreground">{p.default_model || "any model"}</p>
              </button>
            ))}
          </div>
        ) : (
          <form className="space-y-4 px-4" onSubmit={(e) => { e.preventDefault(); save.mutate(undefined); }}>
            <div className="space-y-2">
              <Label htmlFor="pv-name">Name</Label>
              <Input id="pv-name" required value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value.toLowerCase() })} />
            </div>
            <div className="space-y-2">
              <Label htmlFor="pv-model">Default model</Label>
              <ModelPicker key={test?.models?.length ? "live" : "suggested"} id="pv-model" value={form.model}
                onChange={(model) => setForm({ ...form, model })} suggested={preset.models} live={test?.models} />
              <p className="text-xs text-muted-foreground">
                {test?.models?.length ? `${test.models.length} models on this key.` : "Test the connection to list every model on your key."}
              </p>
            </div>
            {preset.type !== "gemini" && (
              <div className="space-y-2">
                <Label htmlFor="pv-url">API endpoint</Label>
                <Input id="pv-url" required value={form.base_url} onChange={(e) => setForm({ ...form, base_url: e.target.value })}
                  className="font-mono text-xs" />
              </div>
            )}
            <div className="space-y-2">
              <Label htmlFor="pv-key">API key{preset.needs_key ? "" : " (optional)"}</Label>
              <Input id="pv-key" type="password" required={preset.needs_key} value={form.api_key}
                onChange={(e) => setForm({ ...form, api_key: e.target.value })} autoComplete="off" />
              <p className="text-xs text-muted-foreground">
                Stored encrypted, never shown again.{" "}
                {preset.key_url && (
                  <a href={preset.key_url} target="_blank" rel="noreferrer" className="inline-flex items-center gap-0.5 text-brand hover:underline">
                    Get a key <ExternalLink className="size-3" />
                  </a>
                )}
              </p>
              {preset.note && <p className="text-xs text-muted-foreground">{preset.note}</p>}
            </div>
            <TestOutcome result={test} />
            {switched && <p className="text-sm text-muted-foreground">{switched}</p>}
            {!test && preset.free_tier && (
              <p className="text-xs text-muted-foreground">
                {preset.label} has a free tier: start on the lightest model, move up only where quality needs it.
              </p>
            )}
            <SheetFooter className="flex-row gap-2 px-0">
              <Button type="button" variant="ghost" onClick={() => setPresetId(null)}>Back</Button>
              <Button type="button" variant="outline" className="ml-auto" onClick={runTest} disabled={testing || !form.model}>
                {testing ? <Loader2 className="animate-spin" /> : <Zap />} Test connection
              </Button>
              <Button type="submit" disabled={save.isPending}>Save provider</Button>
            </SheetFooter>
          </form>
        )}
      </SheetContent>
    </Sheet>
  );
}

function ChangeModel({ p, open, onOpenChange }: { p: ProviderRow; open: boolean; onOpenChange: (o: boolean) => void }) {
  const presets = useQuery(presetsQuery);
  const [model, setModel] = useState(p.default_model);
  const [switched, setSwitched] = useState<string | null>(null);
  // the key's live model list: one tiny test request, made when the dialog opens
  const live = useQuery({
    queryKey: ["provider-models", p.name], enabled: open, staleTime: 5 * 60_000,
    queryFn: () => post<TestResult>("/admin/config/test", { name: p.name }),
  });
  const save = useAction(() => patch(`/admin/config/providers/${p.name}`, { default_model: model }), {
    success: `${p.name} now uses ${model}`, invalidate: [["providers"], ["aliases"]], onSuccess: () => onOpenChange(false),
  });
  const suggested = (p.preset && presets.data?.[p.preset]?.models) || [p.default_model];
  return (
    <Dialog open={open} onOpenChange={(o) => { onOpenChange(o); if (o) { setModel(p.default_model); setSwitched(null); } }}>
      <DialogContent>
        <form onSubmit={(e) => { e.preventDefault(); save.mutate(undefined); }} className="space-y-4">
          <DialogHeader>
            <DialogTitle>Change {p.name} model</DialogTitle>
            <DialogDescription>
              Used when an app or a model on the Models page asks for {p.name} without naming a model.
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-2">
            <Label htmlFor={`cm-${p.name}`}>Default model</Label>
            {live.isLoading ? (
              <p className="flex items-center gap-2 text-sm text-muted-foreground"><Loader2 className="size-4 animate-spin" /> Loading the models on this key…</p>
            ) : (
              <ModelPicker key={live.data?.models?.length ? "live" : "suggested"} id={`cm-${p.name}`} value={model}
                onChange={(m) => { setModel(m); setSwitched(null); }} suggested={suggested} live={live.data?.models} />
            )}
            {live.data && !live.data.models?.length && (
              <p className="text-xs text-muted-foreground">{p.name} doesn&apos;t list its models; type any model name it accepts.</p>
            )}
            {live.data && model === p.default_model && !switched && live.data.models?.length && !live.data.models.includes(model) && (
              <p className="text-sm text-muted-foreground">
                {model} isn&apos;t available on this key.{" "}
                {live.data.recommended && (
                  <button type="button" className="text-brand hover:underline"
                    onClick={() => setSwitched(adoptRecommended(live.data!, model, setModel))}>
                    Use {live.data.recommended}
                  </button>
                )}
              </p>
            )}
          </div>
          <DialogFooter><Button type="submit" disabled={save.isPending || !model || model === p.default_model}>Save model</Button></DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

function ProviderMenu({ p }: { p: ProviderRow }) {
  const [changeModel, setChangeModel] = useState(false);
  const [rotate, setRotate] = useState(false);
  const [key, setKey] = useState("");
  const [result, setResult] = useState<TestResult | null>(null);
  const invalidate = [["providers"], ["aliases"]];
  const toggle = useAction(() => patch(`/admin/config/providers/${p.name}`, { enabled: !p.enabled }), {
    success: p.enabled ? `${p.name} disabled` : `${p.name} enabled`, invalidate,
  });
  const replace = useAction(() => patch(`/admin/config/providers/${p.name}`, { api_key: key }), {
    success: "Key replaced", invalidate, onSuccess: () => { setRotate(false); setKey(""); },
  });
  const remove = useAction(() => del(`/admin/config/providers/${p.name}`), { success: `${p.name} deleted`, invalidate });
  const test = useAction(() => post<TestResult>("/admin/config/test", { name: p.name }), { onSuccess: setResult });
  return (
    <>
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <Button variant="ghost" size="icon" className="size-8" aria-label={`Actions for ${p.name}`}><MoreHorizontal /></Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="end">
          <DropdownMenuItem onClick={() => test.mutate(undefined)}><Zap /> Test connection</DropdownMenuItem>
          <DropdownMenuItem onClick={() => setChangeModel(true)}><Boxes /> Change model</DropdownMenuItem>
          <DropdownMenuItem onClick={() => setRotate(true)}><KeySquare /> Replace key</DropdownMenuItem>
          <DropdownMenuItem onClick={() => toggle.mutate(undefined)}><Power /> {p.enabled ? "Disable" : "Enable"}</DropdownMenuItem>
          <DropdownMenuSeparator />
          <DropdownMenuItem variant="destructive" onClick={() => remove.mutate(undefined)}><Trash2 /> Delete</DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>
      <ChangeModel p={p} open={changeModel} onOpenChange={setChangeModel} />
      <Dialog open={rotate} onOpenChange={setRotate}>
        <DialogContent>
          <form onSubmit={(e) => { e.preventDefault(); replace.mutate(undefined); }} className="space-y-4">
            <DialogHeader>
              <DialogTitle>Replace {p.name} key</DialogTitle>
              <DialogDescription>The new key is used immediately; the old one stops being used.</DialogDescription>
            </DialogHeader>
            <Input type="password" required value={key} onChange={(e) => setKey(e.target.value)} placeholder="New API key" autoFocus />
            <DialogFooter><Button type="submit" disabled={replace.isPending}>Save key</Button></DialogFooter>
          </form>
        </DialogContent>
      </Dialog>
      <Dialog open={!!result} onOpenChange={(o) => !o && setResult(null)}>
        <DialogContent>
          <DialogHeader><DialogTitle>Test: {p.name}</DialogTitle></DialogHeader>
          <TestOutcome result={result} />
        </DialogContent>
      </Dialog>
    </>
  );
}

function ProviderCard({ p }: { p: ProviderRow }) {
  const tone = !p.enabled ? "off" : p.health === "open" ? "down" : p.health === "half_open" ? "warn" : "ok";
  const label = !p.enabled ? "Disabled" : p.health === "open" ? "Down" : p.health === "half_open" ? "Recovering" : "Healthy";
  return (
    <Card className={cn("gap-3", !p.enabled && "opacity-70")}>
      <CardHeader className="flex flex-row items-start gap-3">
        <div className="grid size-9 shrink-0 place-items-center rounded-md border bg-muted text-sm font-semibold uppercase">
          {p.name.slice(0, 2)}
        </div>
        <div className="min-w-0 flex-1">
          <CardTitle className="truncate">{p.name}</CardTitle>
          <p className="mt-1 flex items-center gap-1.5 text-xs text-muted-foreground"><StatusDot tone={tone} /> {label}</p>
        </div>
        {p.source === "panel" ? <ProviderMenu p={p} /> : <Badge variant="outline" className="font-normal">gateway.toml</Badge>}
      </CardHeader>
      <CardContent className="space-y-1.5 text-sm">
        <div className="flex justify-between gap-3"><span className="text-muted-foreground">Model</span><span className="truncate font-mono text-xs">{p.default_model}</span></div>
        <div className="flex justify-between gap-3"><span className="text-muted-foreground">Key</span><span className="font-mono text-xs">{p.key_hint ?? "none"}</span></div>
        <div className="flex justify-between gap-3"><span className="text-muted-foreground">Endpoint</span><span className="truncate font-mono text-xs">{p.base_url ?? "built in"}</span></div>
      </CardContent>
    </Card>
  );
}

export default function ProvidersPage() {
  const providers = useQuery({ queryKey: ["providers"], queryFn: () => get<ProviderRow[]>("/admin/config/providers") });
  return (
    <>
      <PageHeader title="Providers" description="The AI providers TwinLM can route to. Changes apply immediately, no restart.">
        <AddProvider existing={(providers.data ?? []).map((p) => p.name)} />
      </PageHeader>
      {providers.isLoading ? <RowsSkeleton /> : (
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {providers.data?.map((p) => <ProviderCard key={p.name} p={p} />)}
        </div>
      )}
      {!providers.isLoading && !providers.data?.length && (
        <div className="text-sm text-muted-foreground"><Plug className="mr-1 inline size-4" /> No providers yet.</div>
      )}
    </>
  );
}
