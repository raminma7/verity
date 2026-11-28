"use client";

import { Logo } from "@/components/logo";
import { DeletionBanners } from "@/components/privacy";
import { t as tr, msg } from "@/i18n";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useRef, useState, type ReactNode } from "react";

import { api } from "@/lib/api";
import { useSession } from "@/lib/session";

import { Avatar, Button, compact, cx, Icon, type IconName } from "./ui";
import { roleName } from "@/lib/roles";

type NavItem = { href: string; label: string; icon: IconName; perm?: string | string[]; soon?: string };

const PRODUCT: NavItem[] = [
  { href: "/dashboard", label: msg("Dashboard"), icon: "home" },
  { href: "/ask", label: msg("Ask"), icon: "spark", perm: "knowledge.read" },
  { href: "/knowledge", label: msg("Knowledge base"), icon: "book", perm: "knowledge.read" },
  { href: "/chatbots", label: msg("Chatbots"), icon: "chat", perm: "chatbots.read" },
  { href: "/conversations", label: msg("Conversations"), icon: "clock", perm: "chatbots.read" },
  { href: "/tickets", label: msg("Tickets"), icon: "tag", perm: "tickets.read" },
  { href: "/forms", label: msg("Forms"), icon: "file", perm: "chatbots.read" },
  { href: "/email", label: msg("Email"), icon: "mail", perm: ["integrations.manage", "email.review"] },
  { href: "/tools", label: msg("API tools"), icon: "link", perm: "integrations.manage" },
];

const MANAGE: NavItem[] = [
  { href: "/analytics", label: msg("Analytics"), icon: "chart", perm: "analytics.read" },
  { href: "/usage", label: msg("AI usage"), icon: "spark", perm: "usage.read" },
  { href: "/members", label: msg("Members"), icon: "users", perm: "members.read" },
  { href: "/billing", label: msg("Billing"), icon: "card", perm: "billing.read" },
  { href: "/plans", label: msg("Plans"), icon: "sliders" },
  { href: "/settings", label: msg("Settings"), icon: "gear" },
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
          <b>{ws?.name ?? tr("No workspace")}</b>
          <span>{current?.subscription?.active ? current.subscription.plan_name : tr("No plan")}</span>
        </span>
        <Icon name="chevron" />
      </button>
      {open ? (
        <div className="vr-menu" role="menu">
          <span className="vr-eyebrow" style={{ padding: "4px 10px" }}>
            {tr("Workspaces")}
          </span>
          {workspaces.map((w) => (
            <button key={w.id} role="menuitem" type="button" onClick={() => { setOpen(false); if (w.id !== ws?.id) switchWorkspace(w.id); }}>
              <span className="vr-ws-mark" style={{ width: 22, height: 22, fontSize: 11 }}>
                {w.name[0]?.toUpperCase()}
              </span>
              <span style={{ flex: 1, minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{w.name}</span>
              {w.id === ws?.id ? <Icon name="check" /> : <span className="vr-caption vr-muted">{roleName(w.role.name)}</span>}
            </button>
          ))}
          <hr />
          <button role="menuitem" type="button" onClick={() => { setOpen(false); router.push("/settings/workspace?new=1"); }}>
            <Icon name="plus" /> {tr("New workspace")}
          </button>
        </div>
      ) : null}
    </div>
  );
}

function useAvailability(): ["available" | "away" | null, () => void] {
  const { current, can } = useSession();
  const [value, setValue] = useState<"available" | "away" | null>(null);
  const works = can("tickets.manage") && current?.subscription?.active;
  const ws = current?.workspace.id;
  useEffect(() => {
    if (!works) return setValue(null);
    api
      .get<{ availability: "available" | "away" }>("/routing/me")
      .then((r) => setValue(r.availability))
      .catch(() => setValue(null));
  }, [works, ws]);
  const toggle = () => {
    const next = value === "away" ? "available" : "away";
    api
      .put<{ availability: "available" | "away" }>("/routing/me", { availability: next })
      .then((r) => setValue(r.availability))
      .catch(() => {});
  };
  return [value, toggle];
}

function UserMenu() {
  const { user, current } = useSession();
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [theme, toggleTheme] = useTheme();
  const [availability, toggleAvailability] = useAvailability();
  const ref = useOutside(open, () => setOpen(false));
  return (
    <div className="vr-ws" ref={ref}>
      {open ? (
        <div className="vr-menu" role="menu" style={{ top: "auto", bottom: "calc(100% + 6px)" }}>
          <Link role="menuitem" href="/settings" onClick={() => setOpen(false)}>
            <Icon name="gear" /> {tr("Account settings")}
          </Link>
          {availability ? (
            <button role="menuitem" type="button" onClick={toggleAvailability}>
              <Icon name="users" /> {availability === "away" ? tr("Set yourself available") : tr("Set yourself away")}
            </button>
          ) : null}
          <button role="menuitem" type="button" onClick={toggleTheme}>
            <Icon name={theme === "dark" ? "sun" : "moon"} /> {theme === "dark" ? tr("Light theme") : tr("Dark theme")}
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
            <Icon name="logout" /> {tr("Sign out")}
          </button>
        </div>
      ) : null}
      <div style={{ display: "flex", alignItems: "center", gap: 10, padding: "0 8px" }}>
        <Avatar name={user.full_name} />
        <span style={{ display: "flex", flexDirection: "column", flex: 1, minWidth: 0 }}>
          <span style={{ fontSize: 13, fontWeight: 500, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{user.full_name}</span>
          <span className="vr-muted" style={{ fontSize: 12 }}>
            {(current ? roleName(current.role.name) : null) ?? (user.is_system_admin ? tr("System administrator") : tr("No workspace"))}
            {availability ? (
              <>
                <br />
                <span className="vr-avail" aria-label={availability === "away" ? tr("You are away") : tr("You are available")}>
                  <span className={availability === "away" ? "vr-avail-dot is-away" : "vr-avail-dot"} aria-hidden="true" />
                  {availability === "away" ? tr("Away") : tr("Available")}
                </span>
              </>
            ) : null}
          </span>
        </span>
        <Button variant="ghost" size="sm" icon="dots" aria-label={tr("Account menu")} aria-expanded={open} onClick={() => setOpen(!open)} />
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
      <div style={{ display: "flex", flexWrap: "wrap", justifyContent: "space-between", columnGap: 8, fontSize: 12, lineHeight: "16px" }}>
        <span className="vr-muted">{tr("AI tokens")}</span>
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
        .filter((i) => !i.perm || (Array.isArray(i.perm) ? i.perm.some(can) : can(i.perm)))
        .map((i) =>
          i.soon ? (
            <a key={i.href} className="is-disabled" aria-disabled="true" title={tr("Arrives in {soon}", { soon: i.soon })}>
              <Icon name={i.icon} size={18} />
              <span>{tr(i.label)}</span>
              <span className="vr-soon">{tr("Soon")}</span>
            </a>
          ) : (
            <Link key={i.href} href={i.href} onClick={onNavigate} className={cx(pathname.startsWith(i.href) && "is-on")} aria-current={pathname.startsWith(i.href) ? "page" : undefined}>
              <Icon name={i.icon} size={18} />
              <span>{tr(i.label)}</span>
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
        <Link href="/dashboard" className="vr-logo-link">
          <Logo size={24} />
        </Link>
        <Button variant="ghost" icon="menu" aria-label={tr("Open navigation")} onClick={() => setOpen(true)} />
      </div>
      {open ? <div className="vr-scrim" onClick={close} /> : null}
      <nav className="vr-side" aria-label={tr("Main")}>
        <Link href="/dashboard" className="vr-logo-link" style={{ padding: "0 8px" }} onClick={close}>
          <Logo size={30} />
        </Link>
        <WorkspaceSwitcher />
        <div className="vr-side-nav">
          <NavList items={PRODUCT} pathname={pathname} onNavigate={close} />
        </div>
        <div className="vr-side-nav">
          <span className="vr-eyebrow" style={{ padding: "0 12px 4px" }}>
            {tr("Manage")}
          </span>
          <NavList items={MANAGE} pathname={pathname} onNavigate={close} />
          {user.is_system_admin ? (
            <Link href="/admin" onClick={close} className={cx(pathname.startsWith("/admin") && "is-on")}>
              <Icon name="building" size={18} />
              <span>{tr("System admin")}</span>
            </Link>
          ) : null}
        </div>
        <div style={{ flex: 1 }} />
        <TokenMeter />
        <UserMenu />
      </nav>
      <main className="vr-main" id="main">
        <div className="vr-content">
          <DeletionBanners />
          {children}
        </div>
      </main>
    </div>
  );
}
