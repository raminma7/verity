"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useRef, useState, type ReactNode } from "react";

import { api } from "@/lib/api";
import { useSession } from "@/lib/session";

import { Avatar, Button, compact, cx, Icon, type IconName } from "./ui";

type NavItem = { href: string; label: string; icon: IconName; perm?: string; soon?: string };

const PRODUCT: NavItem[] = [
  { href: "/dashboard", label: "Dashboard", icon: "home" },
  { href: "/ask", label: "Ask", icon: "spark", perm: "knowledge.read" },
  { href: "/knowledge", label: "Knowledge base", icon: "book", perm: "knowledge.read" },
  { href: "/chatbots", label: "Chatbots", icon: "chat", perm: "chatbots.read" },
  { href: "/conversations", label: "Conversations", icon: "clock", perm: "chatbots.read" },
  { href: "/tickets", label: "Tickets", icon: "tag", perm: "tickets.read" },
  { href: "/forms", label: "Forms", icon: "file", perm: "chatbots.read" },
  { href: "/inbox", label: "Email", icon: "mail", soon: "Phase 4" },
];

const MANAGE: NavItem[] = [
  { href: "/usage", label: "AI usage", icon: "chart", perm: "usage.read" },
  { href: "/members", label: "Members", icon: "users", perm: "members.read" },
  { href: "/billing", label: "Billing", icon: "card", perm: "billing.read" },
  { href: "/plans", label: "Plans", icon: "sliders" },
  { href: "/settings", label: "Settings", icon: "gear" },
];

function useTheme() {
  const [theme, setTheme] = useState<"light" | "dark">("light");
  useEffect(() => {
    const t = document.documentElement.dataset.theme;
    setTheme(t === "dark" || (!t && matchMedia("(prefers-color-scheme: dark)").matches) ? "dark" : "light");
  }, []);
  return [
    theme,
    () => {
      const next = theme === "dark" ? "light" : "dark";
      document.documentElement.dataset.theme = next;
      try {
        localStorage.setItem("verity.theme", next);
      } catch {}
      setTheme(next);
    },
  ] as const;
}

function useOutside(open: boolean, close: () => void) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => ref.current && !ref.current.contains(e.target as Node) && close();
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && close();
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open, close]);
  return ref;
}

function WorkspaceSwitcher() {
  const { workspaces, current, switchWorkspace } = useSession();
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const ref = useOutside(open, () => setOpen(false));
  const ws = current?.workspace;
  return (
    <div className="vr-ws" ref={ref}>
      <button type="button" className="vr-ws-btn" aria-haspopup="menu" aria-expanded={open} onClick={() => setOpen(!open)}>
        <span className="vr-ws-mark">{ws?.name[0]?.toUpperCase() ?? "?"}</span>
        <span className="vr-ws-text">
          <b>{ws?.name ?? "No workspace"}</b>
          <span>{current?.subscription?.active ? current.subscription.plan_name : "No plan"}</span>
        </span>
        <Icon name="chevron" />
      </button>
      {open ? (
        <div className="vr-menu" role="menu">
          <span className="vr-eyebrow" style={{ padding: "4px 10px" }}>
            Workspaces
          </span>
          {workspaces.map((w) => (
            <button key={w.id} role="menuitem" type="button" onClick={() => { setOpen(false); if (w.id !== ws?.id) switchWorkspace(w.id); }}>
              <span className="vr-ws-mark" style={{ width: 22, height: 22, fontSize: 11 }}>
                {w.name[0]?.toUpperCase()}
              </span>
              <span style={{ flex: 1, minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{w.name}</span>
              {w.id === ws?.id ? <Icon name="check" /> : <span className="vr-caption vr-muted">{w.role.name.replace("Workspace ", "")}</span>}
            </button>
          ))}
          <hr />
          <button role="menuitem" type="button" onClick={() => { setOpen(false); router.push("/settings/workspace?new=1"); }}>
            <Icon name="plus" /> New workspace
          </button>
        </div>
      ) : null}
    </div>
  );
}

function UserMenu() {
  const { user, current } = useSession();
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [theme, toggleTheme] = useTheme();
  const ref = useOutside(open, () => setOpen(false));
  return (
    <div className="vr-ws" ref={ref}>
      {open ? (
        <div className="vr-menu" role="menu" style={{ top: "auto", bottom: "calc(100% + 6px)" }}>
          <Link role="menuitem" href="/settings" onClick={() => setOpen(false)}>
            <Icon name="gear" /> Account settings
          </Link>
          <button role="menuitem" type="button" onClick={toggleTheme}>
            <Icon name={theme === "dark" ? "sun" : "moon"} /> {theme === "dark" ? "Light theme" : "Dark theme"}
          </button>
          <hr />
          <button
            role="menuitem"
            type="button"
            onClick={async () => {
              await api.post("/auth/logout").catch(() => {});
              router.replace("/sign-in");
            }}
          >
            <Icon name="logout" /> Sign out
          </button>
        </div>
      ) : null}
      <div style={{ display: "flex", alignItems: "center", gap: 10, padding: "0 8px" }}>
        <Avatar name={user.full_name} />
        <span style={{ display: "flex", flexDirection: "column", flex: 1, minWidth: 0 }}>
          <span style={{ fontSize: 13, fontWeight: 500, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{user.full_name}</span>
          <span className="vr-muted" style={{ fontSize: 12 }}>
            {current?.role.name ?? (user.is_system_admin ? "System administrator" : "No workspace")}
          </span>
        </span>
        <Button variant="ghost" size="sm" icon="dots" aria-label="Account menu" aria-expanded={open} onClick={() => setOpen(!open)} />
      </div>
    </div>
  );
}

function TokenMeter() {
  const { current, can } = useSession();
  const [usage, setUsage] = useState<{ total: number; used: number } | null>(null);
  const active = current?.subscription?.active;
  useEffect(() => {
    if (!active || !can("usage.read")) return;
    api
      .get<{ active: boolean; total_allowance: number; consumed_weighted: number }>("/usage/summary")
      .then((u) => u.active && setUsage({ total: u.total_allowance, used: u.consumed_weighted }))
      .catch(() => {});
  }, [active, can]);
  if (!usage) return null;
  const pct = usage.total ? Math.min(100, (usage.used / usage.total) * 100) : 0;
  return (
    <Link href="/usage" style={{ display: "flex", flexDirection: "column", gap: 8, padding: 12, borderRadius: "var(--radius-md)", background: "var(--surface-sunken)", textDecoration: "none", color: "var(--ink)" }}>
      <div style={{ display: "flex", justifyContent: "space-between", fontSize: 12, lineHeight: "16px" }}>
        <span className="vr-muted">AI tokens</span>
        <span className="vr-mono">
          {compact(usage.used)} / {compact(usage.total)}
        </span>
      </div>
      <div className={cx("vr-usage-track", pct >= 90 ? "is-danger" : pct >= 80 ? "is-warn" : "")} style={{ height: 4 }}>
        <span style={{ width: `${pct}%` }} />
      </div>
    </Link>
  );
}

function NavList({ items, pathname, onNavigate }: { items: NavItem[]; pathname: string; onNavigate: () => void }) {
  const { can } = useSession();
  return (
    <>
      {items
        .filter((i) => !i.perm || can(i.perm))
        .map((i) =>
          i.soon ? (
            <a key={i.href} className="is-disabled" aria-disabled="true" title={`Arrives in ${i.soon}`}>
              <Icon name={i.icon} size={18} />
              <span>{i.label}</span>
              <span className="vr-soon">Soon</span>
            </a>
          ) : (
            <Link key={i.href} href={i.href} onClick={onNavigate} className={cx(pathname.startsWith(i.href) && "is-on")} aria-current={pathname.startsWith(i.href) ? "page" : undefined}>
              <Icon name={i.icon} size={18} />
              <span>{i.label}</span>
            </Link>
          ),
        )}
    </>
  );
}

export function AppShell({ children }: { children: ReactNode }) {
  const pathname = usePathname();
  const { user } = useSession();
  const [open, setOpen] = useState(false);
  const close = () => setOpen(false);

  return (
    <div className={cx("vr-shell", open && "is-open")}>
      <div className="vr-topbar">
        <Link href="/dashboard" className="vr-logo">
          Verity
        </Link>
        <Button variant="ghost" icon="menu" aria-label="Open navigation" onClick={() => setOpen(true)} />
      </div>
      {open ? <div className="vr-scrim" onClick={close} /> : null}
      <nav className="vr-side" aria-label="Main">
        <Link href="/dashboard" className="vr-logo" style={{ padding: "0 8px" }} onClick={close}>
          Verity
        </Link>
        <WorkspaceSwitcher />
        <div className="vr-side-nav">
          <NavList items={PRODUCT} pathname={pathname} onNavigate={close} />
        </div>
        <div className="vr-side-nav">
          <span className="vr-eyebrow" style={{ padding: "0 12px 4px" }}>
            Manage
          </span>
          <NavList items={MANAGE} pathname={pathname} onNavigate={close} />
          {user.is_system_admin ? (
            <Link href="/admin" onClick={close} className={cx(pathname.startsWith("/admin") && "is-on")}>
              <Icon name="building" size={18} />
              <span>System admin</span>
            </Link>
          ) : null}
        </div>
        <div style={{ flex: 1 }} />
        <TokenMeter />
        <UserMenu />
      </nav>
      <main className="vr-main" id="main">
        <div className="vr-content">{children}</div>
      </main>
    </div>
  );
}
