"use client";

import * as React from "react";
import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import {
  LayoutDashboard,
  Library,
  Table2,
  HelpCircle,
  Sparkles,
  Menu,
  X,
  Activity,
  PanelLeftClose,
  PanelLeftOpen,
  Sun,
  Moon,
} from "lucide-react";

import { NavLink } from "@/components/nav-link";
import { api } from "@/lib/api";
import { cn } from "@/lib/utils";

const workLinks = [
  { href: "/", label: "Dashboard", icon: LayoutDashboard, exact: true },
  { href: "/tables", label: "Tables", icon: Table2 },
  { href: "/library", label: "Library", icon: Library },
];

const analyzeLinks = [
  { href: "/ask", label: "Ask", icon: HelpCircle },
  { href: "/discover", label: "Discover", icon: Sparkles },
];

const COLLAPSED_KEY = "catena-sidebar-collapsed";
const THEME_KEY = "catena-theme";
const EXPANDED_WIDTH = 260;
const COLLAPSED_WIDTH = 72;

function applyTheme(theme: "light" | "dark") {
  if (typeof document === "undefined") return;
  if (theme === "dark") {
    document.documentElement.classList.add("dark");
  } else {
    document.documentElement.classList.remove("dark");
  }
}

function createPersistentStore<T>(key: string, fallback: T, usePrefers?: boolean) {
  const listeners = new Set<() => void>();
  const notify = () => listeners.forEach((l) => l());

  return {
    subscribe(cb: () => void) {
      listeners.add(cb);
      return () => {
        listeners.delete(cb);
      };
    },
    getSnapshot: (): T => {
      if (typeof window === "undefined") return fallback;
      try {
        const v = localStorage.getItem(key);
        if (v === "true") return true as T;
        if (v === "false") return false as T;
        if (v === "dark" || v === "light") return v as T;
      } catch {}
      if (usePrefers) {
        return (window.matchMedia("(prefers-color-scheme: dark)").matches
          ? "dark"
          : "light") as T;
      }
      return fallback;
    },
    getServerSnapshot: () => fallback,
    set(value: T) {
      try {
        localStorage.setItem(key, String(value));
      } catch {}
      notify();
    },
  };
}

const collapsedStore = createPersistentStore<boolean>(COLLAPSED_KEY, false);
const themeStore = createPersistentStore<"light" | "dark">(THEME_KEY, "light", true);

function SidebarContent({
  collapsed,
  onNavigate,
}: {
  collapsed: boolean;
  onNavigate?: () => void;
}) {
  return (
    <>
      <div
        className={cn(
          "mb-8 flex items-center",
          collapsed ? "justify-center px-0" : "px-3",
        )}
      >
        <Link href="/" onClick={onNavigate} className="flex items-center gap-2.5">
          <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-accent font-serif text-lg font-bold text-accent-text shadow-md">
            c
          </span>
          {!collapsed ? (
            <span className="font-serif text-xl font-semibold tracking-tight text-ink">
              catena
            </span>
          ) : null}
        </Link>
      </div>

      <div className="flex-1 space-y-6">
        <section>
          {!collapsed ? (
            <h3 className="mb-2 px-3 text-[11px] font-semibold uppercase tracking-[0.08em] text-muted">
              Work
            </h3>
          ) : (
            <div className="mb-2 border-t border-border" />
          )}
          <nav className="space-y-0.5">
            {workLinks.map((link) => (
              <NavLink key={link.href} {...link} collapsed={collapsed} />
            ))}
          </nav>
        </section>

        <section>
          {!collapsed ? (
            <h3 className="mb-2 px-3 text-[11px] font-semibold uppercase tracking-[0.08em] text-muted">
              Analyze
            </h3>
          ) : (
            <div className="mb-2 border-t border-border" />
          )}
          <nav className="space-y-0.5">
            {analyzeLinks.map((link) => (
              <NavLink key={link.href} {...link} collapsed={collapsed} />
            ))}
          </nav>
        </section>
      </div>

      <HealthPill collapsed={collapsed} />
    </>
  );
}

function HealthPill({ collapsed }: { collapsed: boolean }) {
  const health = useQuery({
    queryKey: ["health"],
    queryFn: api.health,
    retry: false,
  });
  const ok = health.data?.ok ?? false;

  return (
    <div className={cn("mt-auto pt-8", collapsed && "flex justify-center")}>
      <div
        title={collapsed ? `API ${ok ? "connected" : "pending"}` : undefined}
        className={cn(
          "flex items-center rounded-lg border text-xs",
          collapsed ? "size-9 justify-center" : "gap-2 px-3 py-2",
          ok
            ? "border-success/40 bg-success-bg text-success"
            : "border-warning/40 bg-warning-bg text-warning",
        )}
      >
        <Activity className="size-3.5 shrink-0" />
        {!collapsed ? (
          <span className="font-semibold">API {ok ? "connected" : "pending"}</span>
        ) : null}
      </div>
    </div>
  );
}

export function AppShell({ children }: { children: React.ReactNode }) {
  const [mobileOpen, setMobileOpen] = React.useState(false);

  const collapsed = React.useSyncExternalStore(
    collapsedStore.subscribe,
    collapsedStore.getSnapshot,
    collapsedStore.getServerSnapshot,
  );
  const theme = React.useSyncExternalStore(
    themeStore.subscribe,
    themeStore.getSnapshot,
    themeStore.getServerSnapshot,
  );

  React.useEffect(() => {
    applyTheme(theme);
  }, [theme]);

  const toggleCollapsed = React.useCallback(() => {
    collapsedStore.set(!collapsed);
  }, [collapsed]);

  const toggleTheme = React.useCallback(() => {
    themeStore.set(theme === "dark" ? "light" : "dark");
  }, [theme]);

  const sidebarWidth = collapsed ? COLLAPSED_WIDTH : EXPANDED_WIDTH;

  return (
    <div
      className="flex min-h-screen"
      style={{ ["--sidebar-w" as string]: `${sidebarWidth}px` }}
    >
      <aside
        className={cn(
          "fixed inset-y-0 left-0 hidden flex-col border-r border-border-strong bg-surface p-4 md:flex",
          "transition-[width] duration-200 ease-in-out",
        )}
        style={{ width: sidebarWidth }}
      >
        <SidebarContent collapsed={collapsed} />

        <div className="mt-4 space-y-1">
          <ThemeToggleButton
            collapsed={collapsed}
            theme={theme}
            onToggle={toggleTheme}
          />
          <button
            onClick={toggleCollapsed}
            title={collapsed ? "Expand sidebar" : "Collapse sidebar"}
            className={cn(
              "flex items-center gap-3 rounded-lg px-3 py-2 text-xs font-semibold text-muted transition hover:bg-raised hover:text-ink",
              collapsed ? "justify-center px-0" : "w-full",
            )}
          >
            {collapsed ? (
              <PanelLeftOpen className="size-4" />
            ) : (
              <>
                <PanelLeftClose className="size-4" />
                <span>Collapse</span>
              </>
            )}
          </button>
        </div>
      </aside>

      <header className="fixed left-0 right-0 top-0 z-40 flex h-14 items-center justify-between border-b border-border-strong bg-surface px-4 md:hidden">
        <Link href="/" className="flex items-center gap-2.5">
          <span className="flex h-7 w-7 items-center justify-center rounded-lg bg-accent font-serif text-base font-bold text-accent-text">
            c
          </span>
          <span className="font-serif text-xl font-semibold text-ink">catena</span>
        </Link>
        <div className="flex items-center gap-1">
          <button
            className="inline-flex h-10 w-10 items-center justify-center rounded-lg text-ink hover:bg-raised"
            onClick={toggleTheme}
            aria-label="Toggle theme"
          >
            {theme === "dark" ? <Sun className="size-5" /> : <Moon className="size-5" />}
          </button>
          <button
            className="inline-flex h-10 w-10 items-center justify-center rounded-lg text-ink hover:bg-raised"
            onClick={() => setMobileOpen((open) => !open)}
            aria-label="Toggle navigation"
          >
            {mobileOpen ? <X className="size-5" /> : <Menu className="size-5" />}
          </button>
        </div>
      </header>

      {mobileOpen ? (
        <>
          <div
            className="fixed inset-0 z-30 bg-ink/20 backdrop-blur-[2px] animate-fade-in md:hidden"
            onClick={() => setMobileOpen(false)}
          />
          <div className="animate-slide-in-right fixed inset-y-0 right-0 z-40 flex w-[82%] max-w-[320px] flex-col border-l border-border-strong bg-surface p-5 pt-20 md:hidden">
            <button
              className="absolute right-3 top-3 inline-flex h-10 w-10 items-center justify-center rounded-lg text-muted hover:bg-raised hover:text-ink"
              onClick={() => setMobileOpen(false)}
              aria-label="Close navigation"
            >
              <X className="size-5" />
            </button>
            <SidebarContent
              collapsed={false}
              onNavigate={() => setMobileOpen(false)}
            />
          </div>
        </>
      ) : null}

      <main className="min-w-0 flex-1 pt-14 md:ml-[var(--sidebar-w)] md:pt-0">
        <div className="mx-auto max-w-7xl p-5 md:p-8">{children}</div>
      </main>
    </div>
  );
}

function ThemeToggleButton({
  collapsed,
  theme,
  onToggle,
}: {
  collapsed: boolean;
  theme: "light" | "dark";
  onToggle: () => void;
}) {
  return (
    <button
      onClick={onToggle}
      title={theme === "dark" ? "Switch to light mode" : "Switch to dark mode"}
      className={cn(
        "flex items-center gap-3 rounded-lg px-3 py-2 text-xs font-semibold text-muted transition hover:bg-raised hover:text-ink",
        collapsed ? "justify-center px-0" : "w-full",
      )}
    >
      {theme === "dark" ? <Sun className="size-4" /> : <Moon className="size-4" />}
      {collapsed ? null : <span>{theme === "dark" ? "Light mode" : "Dark mode"}</span>}
    </button>
  );
}
