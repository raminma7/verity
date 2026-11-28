"use client";

import { useState, type FormEvent } from "react";

import { Button } from "@/components/ui";
import type { Locale } from "@/i18n";

type Copy = { name: string; email: string; message: string; send: string; sending: string; sent: string; error: string; limited: string; required: string };

/** The contact form: it goes to POST /api/public/contact, which emails the team. A hidden field catches bots. */
export function ContactForm({ locale, copy }: { locale: Locale; copy: Copy }) {
  const [state, setState] = useState<"idle" | "busy" | "sent" | "error" | "limited" | "required">("idle");
  async function submit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const f = new FormData(e.currentTarget);
    const body = { name: String(f.get("name") ?? "").trim(), email: String(f.get("email") ?? "").trim(), message: String(f.get("message") ?? "").trim(), locale, website: String(f.get("website") ?? "") };
    if (!body.name || !body.email || !body.message) return setState("required");
    setState("busy");
    try {
      const r = await fetch("/api/public/contact", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
      setState(r.ok ? "sent" : r.status === 429 ? "limited" : "error");
      if (r.ok) e.currentTarget.reset();
    } catch {
      setState("error");
    }
  }
  const message = state === "sent" ? copy.sent : state === "error" ? copy.error : state === "limited" ? copy.limited : state === "required" ? copy.required : null;
  return (
    <form className="vr-card vr-land-pad vr-form" onSubmit={submit} noValidate>
      <label className="vr-field">
        <span>{copy.name}</span>
        <input className="vr-input" name="name" autoComplete="name" maxLength={120} required />
      </label>
      <label className="vr-field">
        <span>{copy.email}</span>
        <input className="vr-input" name="email" type="email" autoComplete="email" maxLength={254} required />
      </label>
      <label className="vr-field">
        <span>{copy.message}</span>
        <textarea className="vr-input" name="message" rows={5} maxLength={4000} required />
      </label>
      <div className="vr-land-trap" aria-hidden="true">
        <input name="website" tabIndex={-1} autoComplete="off" />
      </div>
      <div className="vr-actions">
        <Button type="submit" variant="primary" busy={state === "busy"}>
          {state === "busy" ? copy.sending : copy.send}
        </Button>
        <span role="status" className={state === "sent" ? "vr-help" : "vr-help vr-help-error"}>
          {message}
        </span>
      </div>
    </form>
  );
}
