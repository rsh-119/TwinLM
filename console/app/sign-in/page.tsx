"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowRight, Loader2, ShieldCheck } from "lucide-react";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";
import { Logo } from "@/components/common";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { ApiError, get, post } from "@/lib/api";
import type { Me } from "@/lib/types";

function Field({ id, label, hint, ...props }: React.ComponentProps<typeof Input> & { label: string; hint?: string }) {
  return (
    <div className="space-y-2">
      <Label htmlFor={id}>{label}</Label>
      <Input id={id} className="h-10" {...props} />
      {hint && <p className="text-xs text-muted-foreground">{hint}</p>}
    </div>
  );
}

function useSubmit(action: () => Promise<void>) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      await action();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Something went wrong");
    } finally {
      setBusy(false);
    }
  };
  return { busy, error, submit };
}

function SubmitButton({ busy, children }: { busy: boolean; children: React.ReactNode }) {
  return (
    <Button type="submit" size="lg" className="h-10 w-full" disabled={busy}>
      {busy ? <Loader2 className="animate-spin" /> : null}
      {children}
      {!busy && <ArrowRight />}
    </Button>
  );
}

function ErrorNote({ error }: { error: string }) {
  if (!error) return null;
  return (
    <Alert variant="destructive">
      <AlertDescription>{error}</AlertDescription>
    </Alert>
  );
}

function SignInForm({ onDone }: { onDone: (me: Me) => void }) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const { busy, error, submit } = useSubmit(async () => {
    const res = await post<{ user: Me }>("/auth/login", { email, password });
    onDone(res.user);
  });
  return (
    <form onSubmit={submit} className="space-y-5">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Sign in</h1>
        <p className="mt-1 text-sm text-muted-foreground">Welcome back. Use the email your admin added you with.</p>
      </div>
      <Field id="email" label="Email" type="email" autoComplete="email" required autoFocus
        value={email} onChange={(e) => setEmail(e.target.value)} placeholder="you@company.com" />
      <Field id="password" label="Password" type="password" autoComplete="current-password" required
        value={password} onChange={(e) => setPassword(e.target.value)} />
      <ErrorNote error={error} />
      <SubmitButton busy={busy}>Sign in</SubmitButton>
      <p className="text-center text-xs text-muted-foreground">
        Forgot your password? Ask a team admin to reset it.
      </p>
    </form>
  );
}

function SetupForm({ onDone }: { onDone: () => void }) {
  const [form, setForm] = useState({ admin_token: "", name: "", email: "", password: "" });
  const set = (k: keyof typeof form) => (e: React.ChangeEvent<HTMLInputElement>) => setForm({ ...form, [k]: e.target.value });
  const { busy, error, submit } = useSubmit(async () => {
    await post("/auth/setup", form);
    onDone();
  });
  return (
    <form onSubmit={submit} className="space-y-5">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Set up TwinLM</h1>
        <p className="mt-1 text-sm text-muted-foreground">Create the first admin account. You only do this once.</p>
      </div>
      <Field id="token" label="Admin token" type="password" required value={form.admin_token} onChange={set("admin_token")}
        hint="Run: docker compose exec gateway cat /run/secrets/admin_token" />
      <div className="grid gap-4 sm:grid-cols-2">
        <Field id="name" label="Your name" required value={form.name} onChange={set("name")} autoComplete="name" />
        <Field id="email" label="Work email" type="email" required value={form.email} onChange={set("email")} autoComplete="email" />
      </div>
      <Field id="password" label="Password" type="password" required minLength={8} value={form.password}
        onChange={set("password")} autoComplete="new-password" hint="At least 8 characters." />
      <ErrorNote error={error} />
      <SubmitButton busy={busy}>Create admin account</SubmitButton>
    </form>
  );
}

function PasswordForm({ onDone }: { onDone: () => void }) {
  const [form, setForm] = useState({ current: "", next: "", again: "" });
  const set = (k: keyof typeof form) => (e: React.ChangeEvent<HTMLInputElement>) => setForm({ ...form, [k]: e.target.value });
  const { busy, error, submit } = useSubmit(async () => {
    if (form.next !== form.again) throw new Error("The new passwords don't match.");
    await post("/auth/password", { current_password: form.current, new_password: form.next });
    onDone();
  });
  return (
    <form onSubmit={submit} className="space-y-5">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Choose your password</h1>
        <p className="mt-1 text-sm text-muted-foreground">You signed in with a temporary password. Pick your own to continue.</p>
      </div>
      <Field id="current" label="Temporary password" type="password" required value={form.current} onChange={set("current")}
        autoComplete="current-password" />
      <Field id="next" label="New password" type="password" required minLength={8} value={form.next} onChange={set("next")}
        autoComplete="new-password" hint="At least 8 characters." />
      <Field id="again" label="Repeat new password" type="password" required value={form.again} onChange={set("again")}
        autoComplete="new-password" />
      <ErrorNote error={error} />
      <SubmitButton busy={busy}>Save and continue</SubmitButton>
    </form>
  );
}

function BrandPanel() {
  return (
    <div className="relative hidden flex-col justify-between overflow-hidden bg-zinc-950 p-10 text-zinc-50 lg:flex">
      <Logo className="text-lg text-zinc-50" />
      <div className="space-y-8">
        <blockquote className="max-w-md text-3xl font-semibold leading-tight tracking-tight">
          Every model your company uses, one key per team, and a bill that goes down.
        </blockquote>
        <div className="grid max-w-md grid-cols-3 gap-3">
          {[
            ["One API", "OpenAI-compatible, every provider"],
            ["Per team", "Keys, limits and spend"],
            ["Savings", "Routing, caching, caps"],
          ].map(([t, d]) => (
            <div key={t} className="rounded-lg border border-zinc-800 bg-zinc-900/60 p-3">
              <p className="text-sm font-medium">{t}</p>
              <p className="mt-1 text-xs text-zinc-400">{d}</p>
            </div>
          ))}
        </div>
      </div>
      <p className="flex items-center gap-2 text-xs text-zinc-400">
        <ShieldCheck className="size-4" /> Self-hosted. Your prompts and keys stay on your infrastructure.
      </p>
      <div className="pointer-events-none absolute -right-24 -top-24 size-80 rounded-full bg-brand/25 blur-3xl" aria-hidden />
    </div>
  );
}

function SignInFlow() {
  const router = useRouter();
  const qc = useQueryClient();
  const params = useSearchParams();
  const setup = useQuery({ queryKey: ["setup"], queryFn: () => get<{ needs_setup: boolean }>("/auth/setup") });
  const [step, setStep] = useState<"password" | null>(params.get("step") === "password" ? "password" : null);

  const enter = async () => {
    qc.removeQueries();
    router.replace("/");
  };

  let content: React.ReactNode;
  if (setup.isLoading) content = <Loader2 className="mx-auto animate-spin text-muted-foreground" />;
  else if (setup.error instanceof ApiError) content = <ErrorNote error={`Gateway unreachable: ${setup.error.message}`} />;
  else if (setup.data?.needs_setup) content = <SetupForm onDone={enter} />;
  else if (step === "password") content = <PasswordForm onDone={enter} />;
  else content = <SignInForm onDone={(me) => (me.must_change_password ? setStep("password") : enter())} />;

  return (
    <div className="grid min-h-screen lg:grid-cols-2">
      <BrandPanel />
      <div className="flex flex-col p-6 sm:p-10">
        <Logo className="lg:hidden" />
        <div className="mx-auto flex w-full max-w-sm flex-1 flex-col justify-center py-10">{content}</div>
        <p className="text-center text-xs text-muted-foreground">TwinLM · open source, MIT licensed</p>
      </div>
    </div>
  );
}

export default function SignInPage() {
  return (
    <Suspense>
      <SignInFlow />
    </Suspense>
  );
}
