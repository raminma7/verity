"use client";

import { t } from "@/i18n";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";

import { Banner, Button, Field } from "@/components/ui";
import { api, type User } from "@/lib/api";

import { useForm } from "../use-form";

function safeNext(next: string | null) {
  return next && next.startsWith("/") && !next.startsWith("//") ? next : "/dashboard";
}

function SignIn() {
  const router = useRouter();
  const params = useSearchParams();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const f = useForm();
  const next = safeNext(params.get("next"));

  return (
    <form
      noValidate
      onSubmit={(e) => {
        e.preventDefault();
        f.run(async () => {
          const r = await api.post<{ user: User; mfa_required: boolean }>("/auth/login", { email, password });
          if (r.mfa_required) router.push(`/sign-in/2fa?next=${encodeURIComponent(next)}`);
          else if (!r.user.email_verified) router.push(`/verify-email?email=${encodeURIComponent(r.user.email)}`);
          else router.push(next);
        });
      }}
    >
      <div className="vr-auth-title">
        <h1 className="vr-display">{t("Welcome back")}</h1>
        <p className="vr-muted">{t("Sign in to your workspace.")}</p>
      </div>
      {params.get("suspended") ? <Banner tone="danger" title={t("This account is suspended.")}>{t("Contact support if you think this is a mistake.")}</Banner> : null}
      {params.get("reset") ? <Banner title={t("Password changed.")}>{t("Sign in with your new password.")}</Banner> : null}
      {f.error ? <Banner tone="danger">{f.error}</Banner> : null}
      <Field label={t("Work email")} name="email" type="email" autoComplete="email" inputSize="lg" placeholder={t("you@company.com")} value={email} onChange={(e) => setEmail(e.target.value)} error={f.fieldErrors.email} required />
      <Field
        label={t("Password")}
        name="password"
        type="password"
        autoComplete="current-password"
        inputSize="lg"
        value={password}
        onChange={(e) => setPassword(e.target.value)}
        error={f.fieldErrors.password}
        trailing={
          <Link className="vr-link" style={{ fontWeight: 400 }} href="/forgot-password">
            {t("Forgot password?")}
          </Link>
        }
        required
      />
      <Button type="submit" variant="ink" size="lg" busy={f.busy}>
        {t("Sign in")}
      </Button>
      <p className="vr-auth-foot vr-muted">
        {t("New to Verity?")} <Link className="vr-link" href="/sign-up">{t("Create an account")}</Link>
      </p>
    </form>
  );
}

export default function Page() {
  return (
    <Suspense>
      <SignIn />
    </Suspense>
  );
}
