"use client";

import { t } from "@/i18n";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";

import { Banner, Button, Field } from "@/components/ui";
import { api } from "@/lib/api";

import { useForm } from "../use-form";

function Reset() {
  const router = useRouter();
  const token = useSearchParams().get("token") ?? "";
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const f = useForm();
  const mismatch = confirm.length > 0 && confirm !== password;

  if (!token)
    return (
      <div className="vr-auth-box">
        <div className="vr-auth-title">
          <h1 className="vr-display">{t("Link not valid")}</h1>
          <p className="vr-muted">{t("Open the link from the reset email, or request a new one.")}</p>
        </div>
        <Link className="vr-btn vr-btn-primary vr-btn-lg" href="/forgot-password">
          {t("Request a new link")}
        </Link>
      </div>
    );

  return (
    <form
      onSubmit={(e) => {
        e.preventDefault();
        if (mismatch) return;
        f.run(async () => {
          await api.post("/auth/reset-password", { token, password });
          router.push("/sign-in?reset=1");
        });
      }}
    >
      <div className="vr-auth-title">
        <span className="vr-eyebrow">{t("Reset password")}</span>
        <h1 className="vr-display">{t("Choose a new password")}</h1>
        <p className="vr-muted">{t("You’ll be signed out on every other device.")}</p>
      </div>
      {f.error ? <Banner tone="danger">{f.error}</Banner> : null}
      <Field label={t("New password")} name="password" type="password" autoComplete="new-password" value={password} onChange={(e) => setPassword(e.target.value)} error={f.fieldErrors.password} help={t("At least 10 characters, with upper and lower case letters and a number.")} />
      <Field label={t("Confirm new password")} name="confirm" type="password" autoComplete="new-password" value={confirm} onChange={(e) => setConfirm(e.target.value)} error={mismatch ? t("The passwords don’t match.") : null} />
      <Button type="submit" variant="primary" size="lg" busy={f.busy} disabled={!password || mismatch}>
        {t("Save password")}
      </Button>
    </form>
  );
}

export default function Page() {
  return (
    <Suspense>
      <Reset />
    </Suspense>
  );
}
