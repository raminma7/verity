"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import type { ReactNode } from "react";

import { PageHeader } from "@/components/ui";
import { useSession } from "@/lib/session";

export default function EmailLayout({ children }: { children: ReactNode }) {
  const path = usePathname();
  const { can } = useSession();
  const tabs = [
    ...(can("integrations.manage") ? [{ href: "/email", label: "Mailboxes" }] : []),
    ...(can("email.review") ? [{ href: "/email/spam", label: "Spam" }] : []),
    ...(can("integrations.manage") ? [{ href: "/settings/email", label: "Email settings" }] : []),
  ];
  return (
    <>
      <PageHeader
        eyebrow="Email"
        title="Email"
        subtitle="Connected mailboxes turn customer email into tickets. Spam and promotions wait in Spam until someone reviews them."
      />
      <nav className="vr-tabs" aria-label="Email sections">
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
