"use client";

import { t as tr } from "@/i18n";
import Link from "next/link";
import { usePathname } from "next/navigation";
import type { ReactNode } from "react";

import { PageHeader } from "@/components/ui";
import { useSession } from "@/lib/session";

export default function EmailLayout({ children }: { children: ReactNode }) {
  const path = usePathname();
  const { can } = useSession();
  const tabs = [
    ...(can("integrations.manage") ? [{ href: "/email", label: tr("Mailboxes") }] : []),
    ...(can("email.review") ? [{ href: "/email/spam", label: tr("Spam") }] : []),
    ...(can("integrations.manage") ? [{ href: "/settings/email", label: tr("Email settings") }] : []),
  ];
  return (
    <>
      <PageHeader
        eyebrow={tr("Email")}
        title={tr("Email")}
        subtitle={tr("Connected mailboxes turn customer email into tickets. Spam and promotions wait in Spam until someone reviews them.")}
      />
      <nav className="vr-tabs" aria-label={tr("Email sections")}>
        {tabs.map((t) => (
          <Link key={t.href} href={t.href} className={path === t.href ? "is-on" : undefined} aria-current={path === t.href ? "page" : undefined}>
            {t.label}
          </Link>
        ))}
      </nav>
      {children}
    </>
  );
}
