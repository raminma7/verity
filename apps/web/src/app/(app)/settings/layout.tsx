"use client";

import { t as tr, msg } from "@/i18n";
import Link from "next/link";
import { usePathname } from "next/navigation";
import type { ReactNode } from "react";

import { PageHeader } from "@/components/ui";

const TABS = [
  { href: "/settings", label: msg("Profile") },
  { href: "/settings/security", label: msg("Security") },
  { href: "/settings/notifications", label: msg("Notifications") },
  { href: "/settings/workspace", label: msg("Workspace") },
  { href: "/settings/tickets", label: msg("Tickets") },
  { href: "/settings/routing", label: msg("Routing") },
  { href: "/settings/email", label: msg("Email") },
];

export default function SettingsLayout({ children }: { children: ReactNode }) {
  const path = usePathname();
  return (
    <>
      <PageHeader eyebrow={tr("Settings")} title={tr("Settings")} />
      <nav className="vr-tabs" aria-label={tr("Settings sections")}>
        {TABS.map((t) => (
          <Link key={t.href} href={t.href} className={path === t.href ? "is-on" : undefined} aria-current={path === t.href ? "page" : undefined}>
            {t.label}
          </Link>
        ))}
      </nav>
      {children}
    </>
  );
}
