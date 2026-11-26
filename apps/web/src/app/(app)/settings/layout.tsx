"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import type { ReactNode } from "react";

import { PageHeader } from "@/components/ui";

const TABS = [
  { href: "/settings", label: "Profile" },
  { href: "/settings/security", label: "Security" },
  { href: "/settings/notifications", label: "Notifications" },
  { href: "/settings/workspace", label: "Workspace" },
];

export default function SettingsLayout({ children }: { children: ReactNode }) {
  const path = usePathname();
  return (
    <>
      <PageHeader eyebrow="Settings" title="Settings" />
      <nav className="vr-tabs" aria-label="Settings sections">
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
