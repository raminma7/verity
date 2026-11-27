"use client";

import { t } from "@/i18n";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";

import { Banner, Button, Field, Icon } from "@/components/ui";
import { api } from "@/lib/api";

import { useForm } from "../../use-form";

function TwoFactor() {
  const router = useRouter();
  const params = useSearchParams();
  const [code, setCode] = useState("");
  const [recovery, setRecovery] = useState(false);
  const f = useForm();
  const next = params.get("next")?.startsWith("/") ? params.get("next")! : "/dashboard";

  return (
    <form
      onSubmit={(e) => {
        e.preventDefault();
        f.run(async () => {
          await api.post("/auth/2fa", { code });
          router.push(next);
        });
      }}
    >
      <span className="vr-auth-icon">
        <Icon name="shield" size={24} />
      </span>
      <div className="vr-auth-title">
        <h1 className="vr-display">{t("Two-factor check")}</h1>
        <p className="vr-muted">
          {recovery ? t("Enter one of your saved recovery codes.") : t("Enter the 6-digit code from your authenticator app.")}
        </p>
      </div>
      {f.error ? <Banner tone="danger">{f.error}</Banner> : null}
      <Field
        label={recovery ? t("Recovery code") : t("Code")}
        name="code"
        inputMode={recovery ? "text" : "numeric"}
        autoComplete="one-time-code"
        className={recovery ? undefined : "vr-otp-field"}
        inputSize="lg"
        maxLength={recovery ? 20 : 6}
        value={code}
        onChange={(e) => setCode(recovery ? e.target.value : e.target.value.replace(/\D/g, ""))}
        autoFocus
      />
      <Button type="submit" variant="primary" size="lg" busy={f.busy} disabled={code.length < 6}>
        {t("Verify and continue")}
      </Button>
      <button type="button" className="vr-link vr-auth-foot" style={{ background: "none", border: 0, cursor: "pointer" }} onClick={() => { setRecovery(!recovery); setCode(""); }}>
        {recovery ? t("Use your authenticator app instead") : t("Lost your device? Use a recovery code")}
      </button>
    </form>
  );
}

export default function Page() {
  return (
    <Suspense>
      <TwoFactor />
    </Suspense>
  );
}
