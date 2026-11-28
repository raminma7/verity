"use client";

import { t } from "@/i18n";
import type { ReactNode } from "react";

import { AppShell } from "@/components/app-shell";
import { Spinner } from "@/components/ui";
import { SessionProvider } from "@/lib/session";

export default function AppLayout({ children }: { children: ReactNode }) {
  return (
    <SessionProvider fallback={<Spinner label={t("Opening Verity")} />}>
      <AppShell>{children}</AppShell>
    </SessionProvider>
  );
}
