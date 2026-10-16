"use client";

import { useState } from "react";
import { PageHeader, RoleBadge } from "@/components/common";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardFooter, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { post } from "@/lib/api";
import { useAction, useMe } from "@/lib/hooks";

export default function AccountPage() {
  const { data: me } = useMe();
  const [form, setForm] = useState({ current: "", next: "", again: "" });
  const change = useAction(
    () => {
      if (form.next !== form.again) return Promise.reject(new Error("The new passwords don't match."));
      return post("/auth/password", { current_password: form.current, new_password: form.next });
    },
    { success: "Password changed", onSuccess: () => setForm({ current: "", next: "", again: "" }) },
  );
  if (!me) return null;
  return (
    <>
      <PageHeader title="Account" />
      <Card>
        <CardHeader>
          <CardTitle>Profile</CardTitle>
          <CardDescription>Ask an admin to change your name or email.</CardDescription>
        </CardHeader>
        <CardContent className="grid gap-4 sm:grid-cols-3">
          <div><p className="text-sm text-muted-foreground">Name</p><p className="font-medium">{me.name}</p></div>
          <div><p className="text-sm text-muted-foreground">Email</p><p className="font-medium">{me.email}</p></div>
          <div><p className="text-sm text-muted-foreground">Role</p><RoleBadge role={me.role} /></div>
          <div className="sm:col-span-3">
            <p className="text-sm text-muted-foreground">Teams</p>
            <p className="font-medium">{me.teams.map((t) => `${t.name} (${t.role === "admin" ? "team admin" : "member"})`).join(", ") || "None"}</p>
          </div>
        </CardContent>
      </Card>
      <Card>
        <form onSubmit={(e) => { e.preventDefault(); change.mutate(undefined); }}>
          <CardHeader>
            <CardTitle>Password</CardTitle>
            <CardDescription>At least 8 characters.</CardDescription>
          </CardHeader>
          <CardContent className="grid max-w-md gap-4 py-4">
            {([["current", "Current password", "current-password"], ["next", "New password", "new-password"],
              ["again", "Repeat new password", "new-password"]] as const).map(([key, label, auto]) => (
              <div key={key} className="space-y-2">
                <Label htmlFor={key}>{label}</Label>
                <Input id={key} type="password" required minLength={key === "current" ? 1 : 8} autoComplete={auto}
                  value={form[key]} onChange={(e) => setForm({ ...form, [key]: e.target.value })} />
              </div>
            ))}
          </CardContent>
          <CardFooter>
            <Button type="submit" disabled={change.isPending}>Change password</Button>
          </CardFooter>
        </form>
      </Card>
    </>
  );
}
