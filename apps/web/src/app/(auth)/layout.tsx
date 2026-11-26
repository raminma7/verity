import type { ReactNode } from "react";

/** The split sign-in layout from the design system's WebAuth screen: teal story panel, form on the right. */
export default function AuthLayout({ children }: { children: ReactNode }) {
  return (
    <div className="vr-auth">
      <section className="vr-auth-story" aria-hidden="true">
        <span className="vr-logo">Verity</span>
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
          <h2 className="vr-display">Support answers you can check.</h2>
          <p>Your knowledge base, chatbot and tickets in one workspace. Every AI answer cites the passage it came from.</p>
        </div>
      </section>
      <main className="vr-auth-form">
        <span className="vr-logo vr-auth-mobile-logo">Verity</span>
        {children}
      </main>
    </div>
  );
}
