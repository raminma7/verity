"use client";

import { useEffect, useState, type ReactNode } from "react";

import { Logo } from "@/components/logo";
import { preferredLocale, setLocale, t } from "@/i18n";

/** The split sign-in layout from the design system's WebAuth screen: teal story panel, form on the right.
 *  Shown once the language remembered on this device (or the browser's) is loaded. */
export default function AuthLayout({ children }: { children: ReactNode }) {
  const [ready, setReady] = useState(false);
  useEffect(() => {
    setLocale(preferredLocale()).then(() => setReady(true));
  }, []);
  if (!ready) return <div className="vr-auth" />;
  return (
    <div className="vr-auth">
      <section className="vr-auth-story" aria-hidden="true">
        <Logo theme="teal" size={32} />
        <div className="vr-auth-sheet">
          <span style={{ width: "92%" }} />
          <span style={{ width: "74%" }} />
          <span style={{ width: "86%" }} />
          <span style={{ display: "flex", gap: 8, alignItems: "center", height: "auto", background: "none" }}>
            <span style={{ height: 10, width: "40%", borderRadius: 5, background: "var(--on-highlight)" }} />
            <span style={{ height: 18, width: 26, borderRadius: 6, background: "var(--on-highlight)" }} />
          </span>
          <span style={{ width: "64%" }} />
        </div>
        <div className="vr-auth-pitch">
          <h2 className="vr-display">{t("Support answers you can check.")}</h2>
          <p>{t("Your knowledge base, chatbot and tickets in one workspace. Every AI answer cites the passage it came from.")}</p>
        </div>
      </section>
      <main className="vr-auth-form">
        <Logo className="vr-auth-mobile-logo" size={26} />
        {children}
      </main>
    </div>
  );
}
