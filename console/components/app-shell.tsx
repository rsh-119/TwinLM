"use client";

import {
  Activity, BarChart3, Boxes, ChevronsUpDown, KeyRound, LayoutDashboard, LogOut, MessageSquare, Monitor, Moon,
  PiggyBank, Plug, Sun, User, Users, UsersRound, Wallet,
} from "lucide-react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useTheme } from "next-themes";
import { useEffect } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { Logo } from "@/components/common";
import { Avatar, AvatarFallback } from "@/components/ui/avatar";
import {
  DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuLabel, DropdownMenuRadioGroup,
  DropdownMenuRadioItem, DropdownMenuSeparator, DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import {
  Sidebar, SidebarContent, SidebarFooter, SidebarGroup, SidebarGroupContent, SidebarGroupLabel, SidebarHeader,
  SidebarInset, SidebarMenu, SidebarMenuButton, SidebarMenuItem, SidebarProvider, SidebarTrigger,
} from "@/components/ui/sidebar";
import { Separator } from "@/components/ui/separator";
import { Skeleton } from "@/components/ui/skeleton";
import { ApiError, post } from "@/lib/api";
import { initials } from "@/lib/format";
import { useMe } from "@/lib/hooks";

const WORKSPACE = [
  { title: "Overview", href: "/", icon: LayoutDashboard },
  { title: "Playground", href: "/playground/", icon: MessageSquare },
  { title: "Teams", href: "/teams/", icon: Users },
];
const ADMIN = [
  { title: "People", href: "/people/", icon: UsersRound },
  { title: "Keys", href: "/keys/", icon: KeyRound },
  { title: "Providers", href: "/providers/", icon: Plug },
  { title: "Models", href: "/models/", icon: Boxes },
  { title: "Usage", href: "/usage/", icon: BarChart3 },
  { title: "Savings", href: "/savings/", icon: PiggyBank },
  { title: "Budgets", href: "/budgets/", icon: Wallet },
  { title: "Health", href: "/health/", icon: Activity },
];

function NavGroup({ label, items, path }: { label: string; items: typeof WORKSPACE; path: string }) {
  return (
    <SidebarGroup>
      <SidebarGroupLabel>{label}</SidebarGroupLabel>
      <SidebarGroupContent>
        <SidebarMenu>
          {items.map((item) => {
            const active = item.href === "/" ? path === "/" : path.startsWith(item.href);
            return (
              <SidebarMenuItem key={item.href}>
                <SidebarMenuButton asChild isActive={active} tooltip={item.title}>
                  <Link href={item.href}>
                    <item.icon className={active ? "text-brand" : undefined} />
                    <span>{item.title}</span>
                  </Link>
                </SidebarMenuButton>
              </SidebarMenuItem>
            );
          })}
        </SidebarMenu>
      </SidebarGroupContent>
    </SidebarGroup>
  );
}

function UserMenu() {
  const { data: me } = useMe();
  const { theme, setTheme } = useTheme();
  const router = useRouter();
  const qc = useQueryClient();
  if (!me) return <Skeleton className="h-12 w-full" />;
  const signOut = async () => {
    await post("/auth/logout").catch(() => undefined);
    qc.clear();
    router.replace("/sign-in/");
  };
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <SidebarMenuButton size="lg" className="data-[state=open]:bg-sidebar-accent">
          <Avatar className="size-8 rounded-md">
            <AvatarFallback className="rounded-md text-xs">{initials(me.name)}</AvatarFallback>
          </Avatar>
          <div className="grid flex-1 text-left text-sm leading-tight">
            <span className="truncate font-medium">{me.name}</span>
            <span className="truncate text-xs text-muted-foreground">{me.email}</span>
          </div>
          <ChevronsUpDown className="ml-auto size-4" />
        </SidebarMenuButton>
      </DropdownMenuTrigger>
      <DropdownMenuContent side="top" align="start" className="w-60">
        <DropdownMenuLabel className="font-normal">
          <p className="text-sm font-medium">{me.name}</p>
          <p className="text-xs text-muted-foreground">{me.role === "admin" ? "Admin" : "Member"}</p>
        </DropdownMenuLabel>
        <DropdownMenuSeparator />
        <DropdownMenuItem asChild>
          <Link href="/account/">
            <User /> Account
          </Link>
        </DropdownMenuItem>
        <DropdownMenuSeparator />
        <DropdownMenuLabel className="text-xs font-normal text-muted-foreground">Theme</DropdownMenuLabel>
        <DropdownMenuRadioGroup value={theme} onValueChange={setTheme}>
          <DropdownMenuRadioItem value="light"><Sun /> Light</DropdownMenuRadioItem>
          <DropdownMenuRadioItem value="dark"><Moon /> Dark</DropdownMenuRadioItem>
          <DropdownMenuRadioItem value="system"><Monitor /> System</DropdownMenuRadioItem>
        </DropdownMenuRadioGroup>
        <DropdownMenuSeparator />
        <DropdownMenuItem onClick={signOut}>
          <LogOut /> Sign out
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

export default function AppShell({ children }: { children: React.ReactNode }) {
  const path = usePathname() || "/";
  const router = useRouter();
  const { data: me, error, isLoading } = useMe();

  // not signed in, or still on a temporary password
  useEffect(() => {
    if (error instanceof ApiError && error.status === 401) router.replace("/sign-in/");
    else if (me?.must_change_password) router.replace("/sign-in/?step=password");
  }, [error, me, router]);

  if (isLoading || !me || me.must_change_password) {
    return (
      <div className="grid min-h-screen place-items-center">
        <Logo className="animate-pulse text-lg" />
      </div>
    );
  }

  return (
    <SidebarProvider>
      <Sidebar collapsible="icon" variant="inset">
        <SidebarHeader>
          <SidebarMenu>
            <SidebarMenuItem>
              <SidebarMenuButton size="lg" asChild>
                <Link href="/">
                  <Logo />
                </Link>
              </SidebarMenuButton>
            </SidebarMenuItem>
          </SidebarMenu>
        </SidebarHeader>
        <SidebarContent>
          <NavGroup label="Workspace" items={WORKSPACE} path={path} />
          {me.role === "admin" && <NavGroup label="Admin" items={ADMIN} path={path} />}
        </SidebarContent>
        <SidebarFooter>
          <SidebarMenu>
            <SidebarMenuItem>
              <UserMenu />
            </SidebarMenuItem>
          </SidebarMenu>
        </SidebarFooter>
      </Sidebar>
      <SidebarInset>
        <header className="flex h-14 shrink-0 items-center gap-2 border-b px-4 md:hidden">
          <SidebarTrigger />
          <Separator orientation="vertical" className="h-4" />
          <Logo />
        </header>
        <div className="flex items-center gap-2 px-6 pt-4 max-md:hidden">
          <SidebarTrigger className="-ml-2" />
        </div>
        <main className="mx-auto w-full max-w-6xl flex-1 space-y-6 px-4 pb-12 pt-5 sm:px-6 md:pt-2">{children}</main>
      </SidebarInset>
    </SidebarProvider>
  );
}
