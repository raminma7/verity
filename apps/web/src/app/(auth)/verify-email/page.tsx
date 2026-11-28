"use client";

import { t as tr } from "@/i18n";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useRef, useState } from "react";

import { Banner, Button, Icon, Spinner } from "@/components/ui";
import { api, ApiError, type User } from "@/lib/api";

import { useForm } from "../use-form";

function CheckInbox({ email }: { email: string }) {
  const [cooldown, setCooldown] = useState(0);
  const [sent, setSent] = useState(false);
  const f = useForm();

  useEffect(() => {
    if (cooldown <= 0) return;
    const t = setTimeout(() => setCooldown((c) => c - 1), 1000);
    return () => clearTimeout(t);
  }, [cooldown]);

  return (
    <div className="vr-auth-box">
      <span className="vr-auth-icon">
        <Icon name="mail" size={24} />
      </span>
      <div className="vr-auth-title">
        <span className="vr-eyebrow">{tr("Create account · 2 of 2")}</span>
        <h1 className="vr-display">{tr("Check your inbox")}</h1>
        <p className="vr-muted">
          {tr("We sent a verification link to {email}. Open it to finish setting up your workspace. The link expires in 24 hours.", { email: email || tr("your email address") })}
        </p>
      </div>
      {sent ? <Banner title={tr("Sent again.")}>{tr("Only the newest link works.")}</Banner> : null}
      {f.error ? <Banner tone="danger">{f.error}</Banner> : null}
      <Button
        variant="secondary"
        size="lg"
        icon="refresh"
        busy={f.busy}
        disabled={cooldown > 0 || !email}
        onClick={() =>
          f.run(async () => {
            await api.post("/auth/resend-verification", { email });
            setSent(true);
            setCooldown(30);
          })
        }
      >
        {cooldown > 0 ? tr("Resend in 0:{v}", { v: String(cooldown).padStart(2, "0") }) : tr("Resend email")}
      </Button>
      <p className="vr-auth-foot vr-muted">
        {tr("Wrong address?")}{" "}
        <Link className="vr-link" href="/sign-up" onClick={() => api.post("/auth/logout").catch(() => {})}>
          {tr("Start again")}
        </Link>
      </p>
    </div>
  );
}

function Confirm({ token }: { token: string }) {
  const router = useRouter();
  const [error, setError] = useState<ApiError | null>(null);
  const started = useRef(false);

  useEffect(() => {
    if (started.current) return;
    started.current = true;
    api
      .post<User>("/auth/verify-email", { token })
      .then((u) => {
        let next: string | null = null;
        try {
          next = localStorage.getItem("verity.next");
          localStorage.removeItem("verity.next");
        } catch {}
        const dest = next?.startsWith("/invite/") ? next : "/dashboard?welcome=1";
        router.replace(u.totp_enabled ? `/sign-in/2fa?next=${encodeURIComponent(dest)}` : dest);
      })
      .catch((e) => setError(e instanceof ApiError ? e : new ApiError(0, {})));
  }, [token, router]);

  if (!error) return <Spinner label={tr("Verifying your email")} />;
  return (
    <div className="vr-auth-box">
      <div className="vr-auth-title">
        <h1 className="vr-display">{error.code === "expired_token" ? tr("Link expired") : tr("Link not valid")}</h1>
        <p className="vr-muted">{error.message}</p>
      </div>
      <p className="vr-muted">{tr("Sign in and we’ll send you a fresh link, or request one below.")}</p>
      <Link className="vr-btn vr-btn-primary vr-btn-lg" href="/sign-in">
        {tr("Go to sign in")}
      </Link>
    </div>
  );
}

function VerifyEmail() {
  const params = useSearchParams();
  const token = params.get("token");
  if (token) return <Confirm token={token} />;
  return <CheckInbox email={params.get("email") ?? ""} />;
}

export default function Page() {
  return (
    <Suspense>
      <VerifyEmail />
    </Suspense>
  );
}
