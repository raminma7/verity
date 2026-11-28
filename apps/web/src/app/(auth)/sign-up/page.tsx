"use client";

import { t } from "@/i18n";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";

import { Banner, Button, Field } from "@/components/ui";
import { api } from "@/lib/api";

import { useForm } from "../use-form";

export default function SignUp() {
  const router = useRouter();
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const f = useForm();

  return (
    <form
      noValidate
      onSubmit={(e) => {
        e.preventDefault();
        f.run(async () => {
          await api.post("/auth/register", { full_name: name, email, password });
          // Keep an invitation link across the email round trip.
          const next = new URLSearchParams(window.location.search).get("next");
          try {
            if (next?.startsWith("/invite/")) localStorage.setItem("verity.next", next);
          } catch {}
          router.push(`/verify-email?email=${encodeURIComponent(email.trim().toLowerCase())}&sent=1`);
        });
      }}
    >
      <div className="vr-auth-title">
        <span className="vr-eyebrow">{t("Create account · 1 of 2")}</span>
        <h1 className="vr-display">{t("Set up your workspace")}</h1>
        <p className="vr-muted">{t("We’ll create a private workspace for your support team once you confirm your email.")}</p>
      </div>
      {f.error ? <Banner tone="danger">{f.error}</Banner> : null}
      <Field label={t("Full name")} name="full_name" autoComplete="name" value={name} onChange={(e) => setName(e.target.value)} error={f.fieldErrors.full_name} required />
      <Field label={t("Work email")} name="email" type="email" autoComplete="email" placeholder={t("you@company.com")} value={email} onChange={(e) => setEmail(e.target.value)} error={f.fieldErrors.email} required />
      <Field
        label={t("Password")}
        name="password"
        type="password"
        autoComplete="new-password"
        value={password}
        onChange={(e) => setPassword(e.target.value)}
        error={f.fieldErrors.password}
        help={t("At least 10 characters, with upper and lower case letters and a number.")}
        required
      />
      <Button type="submit" variant="primary" size="lg" iconRight="arrow" busy={f.busy}>
        {t("Continue")}
      </Button>
      <p className="vr-auth-foot vr-muted">
        {t("Already have an account?")} <Link className="vr-link" href="/sign-in">{t("Sign in")}</Link>
      </p>
    </form>
  );
}
