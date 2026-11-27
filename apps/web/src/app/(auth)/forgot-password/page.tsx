"use client";

import { t } from "@/i18n";
import Link from "next/link";
import { useState } from "react";

import { Banner, Button, Field } from "@/components/ui";
import { api } from "@/lib/api";

import { useForm } from "../use-form";

export default function ForgotPassword() {
  const [email, setEmail] = useState("");
  const [sent, setSent] = useState(false);
  const f = useForm();

  return (
    <form
      noValidate
      onSubmit={(e) => {
        e.preventDefault();
        f.run(async () => {
          await api.post("/auth/forgot-password", { email });
          setSent(true);
        });
      }}
    >
      <div className="vr-auth-title">
        <span className="vr-eyebrow">{t("Reset password")}</span>
        <h1 className="vr-display">{t("Forgot your password?")}</h1>
        <p className="vr-muted">{t("Enter your work email and we’ll send a reset link.")}</p>
      </div>
      {f.error ? <Banner tone="danger">{f.error}</Banner> : null}
      <Field label={t("Work email")} name="email" type="email" autoComplete="email" value={email} onChange={(e) => setEmail(e.target.value)} error={f.fieldErrors.email} required />
      {sent ? <Banner title={t("Check your email.")}>{t("If an account uses that address, the link is on its way. It works for 60 minutes.")}</Banner> : null}
      <Button type="submit" variant="primary" size="lg" busy={f.busy}>
        {sent ? t("Send again") : t("Send reset link")}
      </Button>
      <Link className="vr-link vr-auth-foot" href="/sign-in">
        {t("← Back to sign in")}
      </Link>
    </form>
  );
}
