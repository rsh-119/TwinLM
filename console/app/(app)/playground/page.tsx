"use client";

import { useQuery } from "@tanstack/react-query";
import { ArrowUp, Bot, Eraser, Loader2, MessageSquare, Users } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { Empty, PageHeader } from "@/components/common";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { Textarea } from "@/components/ui/textarea";
import { useTeams } from "@/lib/hooks";
import { cn } from "@/lib/utils";

interface Message {
  role: "user" | "assistant";
  content: string;
  meta?: { provider?: string; model?: string; ms?: number; tokens?: number; cache?: boolean; cut?: boolean; error?: boolean };
}

const headersFor = (team: string) => ({
  "Content-Type": "application/json",
  "X-TwinLM-Console": "1",
  "X-TwinLM-Team": team,
});

export default function PlaygroundPage() {
  const teams = useTeams();
  const [picked, setTeam] = useState<string>("");
  const [model, setModel] = useState("auto");
  const [temperature, setTemperature] = useState(0);
  const [stream, setStream] = useState(true);
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const bottom = useRef<HTMLDivElement>(null);

  const team = picked || teams.data?.[0]?.name || "";
  useEffect(() => {
    bottom.current?.scrollIntoView({ behavior: "smooth" }); // returns a Promise in new browsers: don't return it
  }, [messages]);

  const models = useQuery({
    queryKey: ["models", team],
    enabled: !!team,
    queryFn: async () => {
      const res = await fetch("/v1/models", { headers: headersFor(team), credentials: "same-origin" });
      const body = await res.json();
      return (body.data ?? []).map((m: { id: string }) => m.id) as string[];
    },
  });

  const send = async () => {
    const text = input.trim();
    if (!text || !team || busy) return;
    const history: Message[] = [...messages, { role: "user", content: text }];
    setMessages([...history, { role: "assistant", content: "" }]);
    setInput("");
    setBusy(true);
    const started = performance.now();
    const update = (patch: Partial<Message>) =>
      setMessages((all) => all.map((m, i) => (i === all.length - 1 ? { ...m, ...patch, meta: { ...m.meta, ...patch.meta } } : m)));
    try {
      const res = await fetch("/v1/chat/completions", {
        method: "POST",
        credentials: "same-origin",
        headers: headersFor(team),
        body: JSON.stringify({
          model, temperature, stream,
          messages: history.map(({ role, content }) => ({ role, content })),
          ...(stream ? { stream_options: { include_usage: true } } : {}),
        }),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body?.error?.message || body?.detail || `HTTP ${res.status}`);
      }
      if (stream && res.body) {
        const reader = res.body.getReader();
        const decoder = new TextDecoder();
        let buffer = "";
        let content = "";
        for (;;) {
          const { done, value } = await reader.read();
          if (done) break;
          buffer += decoder.decode(value, { stream: true });
          const lines = buffer.split("\n");
          buffer = lines.pop() ?? "";
          for (const line of lines) {
            if (!line.startsWith("data:") || line.trim() === "data: [DONE]") continue;
            const event = JSON.parse(line.slice(5));
            content += event.delta ?? event.choices?.[0]?.delta?.content ?? "";
            update({
              content,
              meta: {
                provider: event.provider, model: event.model,
                ...(event.usage ? { tokens: event.usage.total_tokens } : {}),
                ...(event.choices?.[0]?.finish_reason === "length" ? { cut: true } : {}),
              },
            });
          }
        }
        update({ meta: { ms: Math.round(performance.now() - started) } });
      } else {
        const body = await res.json();
        update({
          content: body.choices?.[0]?.message?.content ?? "",
          meta: {
            provider: body.provider, model: body.model, tokens: body.usage?.total_tokens,
            ms: Math.round(performance.now() - started), cache: res.headers.get("x-cache") === "HIT",
            cut: body.choices?.[0]?.finish_reason === "length",
          },
        });
      }
    } catch (err) {
      update({ content: err instanceof Error ? err.message : "Request failed", meta: { error: true } });
    } finally {
      setBusy(false);
    }
  };

  if (teams.data && !teams.data.length) {
    return (
      <>
        <PageHeader title="Playground" />
        <Empty icon={Users} title="Join a team first">Usage in the playground is billed to a team. Ask an admin to add you to one.</Empty>
      </>
    );
  }

  return (
    <>
      <PageHeader title="Playground" description="Try models through TwinLM. Usage is billed to the team you choose." />
      <div className="grid gap-4 lg:grid-cols-[1fr_260px]">
        <Card className="flex min-h-[60vh] flex-col py-0">
          <div className="flex-1 space-y-6 overflow-y-auto p-5">
            {!messages.length && (
              <Empty icon={MessageSquare} title="Ask anything">
                Tip: ask the same question twice with temperature 0 and streaming off. The second answer comes from the cache, for $0.
              </Empty>
            )}
            {messages.map((m, i) => (
              <div key={i} className={cn("flex gap-3", m.role === "user" && "justify-end")}>
                {m.role === "assistant" && (
                  <div className="grid size-8 shrink-0 place-items-center rounded-md bg-muted"><Bot className="size-4" /></div>
                )}
                <div className={cn("max-w-[80%] space-y-2", m.role === "user" && "text-right")}>
                  <div className={cn(
                    "whitespace-pre-wrap rounded-lg px-4 py-2.5 text-sm leading-relaxed",
                    m.role === "user" ? "bg-primary text-primary-foreground" : "bg-muted",
                    m.meta?.error && "bg-destructive/10 text-destructive",
                  )}>
                    {m.content || (busy && i === messages.length - 1 ? <Loader2 className="size-4 animate-spin" /> : null)}
                  </div>
                  {m.meta && !m.meta.error && m.meta.provider && (
                    <div className="flex flex-wrap gap-1.5">
                      <Badge variant="secondary" className="font-mono font-normal">{m.meta.provider} · {m.meta.model}</Badge>
                      {m.meta.ms != null && <Badge variant="outline" className="font-normal">{m.meta.ms} ms</Badge>}
                      {m.meta.tokens != null && <Badge variant="outline" className="font-normal">{m.meta.tokens} tokens</Badge>}
                      {m.meta.cache && <Badge className="bg-success font-normal text-white">From cache · $0</Badge>}
                      {m.meta.cut && <Badge variant="outline" className="font-normal">Cut off by max tokens</Badge>}
                    </div>
                  )}
                </div>
              </div>
            ))}
            <div ref={bottom} />
          </div>
          <form
            className="border-t p-3"
            onSubmit={(e) => {
              e.preventDefault();
              send();
            }}
          >
            <div className="relative">
              <Textarea
                value={input}
                onChange={(e) => setInput(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && !e.shiftKey) {
                    e.preventDefault();
                    send();
                  }
                }}
                placeholder="Message… (Enter to send, Shift+Enter for a new line)"
                className="min-h-14 resize-none pr-12"
              />
              <Button type="submit" size="icon" className="absolute bottom-2 right-2 size-8" disabled={busy || !input.trim()}>
                {busy ? <Loader2 className="animate-spin" /> : <ArrowUp />}
                <span className="sr-only">Send</span>
              </Button>
            </div>
          </form>
        </Card>
        <Card className="h-fit">
          <CardContent className="space-y-5">
            <div className="space-y-2">
              <Label>Team</Label>
              <Select value={team} onValueChange={setTeam}>
                <SelectTrigger className="w-full"><SelectValue placeholder="Team" /></SelectTrigger>
                <SelectContent>
                  {teams.data?.map((t) => <SelectItem key={t.id} value={t.name}>{t.name}</SelectItem>)}
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-2">
              <Label>Model</Label>
              <Select value={model} onValueChange={setModel}>
                <SelectTrigger className="w-full font-mono text-xs"><SelectValue /></SelectTrigger>
                <SelectContent>
                  {(models.data?.length ? models.data : ["auto"]).map((id) => (
                    <SelectItem key={id} value={id} className="font-mono text-xs">{id}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-2">
              <div className="flex justify-between">
                <Label htmlFor="temp">Temperature</Label>
                <span className="tabular text-sm text-muted-foreground">{temperature.toFixed(1)}</span>
              </div>
              <input id="temp" type="range" min={0} max={2} step={0.1} value={temperature}
                onChange={(e) => setTemperature(Number(e.target.value))} className="w-full accent-brand" />
              <p className="text-xs text-muted-foreground">0 is deterministic and can be cached.</p>
            </div>
            <div className="flex items-center justify-between">
              <Label htmlFor="stream">Stream</Label>
              <Switch id="stream" checked={stream} onCheckedChange={setStream} />
            </div>
            <Button variant="outline" className="w-full" onClick={() => setMessages([])} disabled={!messages.length}>
              <Eraser /> Clear chat
            </Button>
          </CardContent>
        </Card>
      </div>
    </>
  );
}
