"use client";

import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { Banner, Button, Icon, Spinner } from "@/components/ui";
import { api, ApiError, type User } from "@/lib/api";

import { useForm } from "../../use-form";

type Preview = { workspace: string; role: string; email: string; invited_by: string };

export default function Invite() {
  const { token } = useParams<{ token: string }>();
  const router = useRouter();
  const [preview, setPreview] = useState<Preview | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const [me, setMe] = useState<User | null | undefined>(undefined);
  const f = useForm();

  useEffect(() => {
    api.get<Preview>(`/invitations/${token}`).then(setPreview).catch((e) => setProblem(e instanceof ApiError ? e.message : "This invitation can’t be opened."));
    api.get<{ user: User }>("/auth/session").then((r) => setMe(r.user)).catch(() => setMe(null));
  }, [token]);

  if (problem)
    return (
      <div className="vr-auth-box">
        <div className="vr-auth-title">
          <h1 className="vr-display">Invitation unavailable</h1>
          <p className="vr-muted">{problem}</p>
        </div>
        <Link className="vr-link" href="/sign-in">Go to sign in</Link>
      </div>
    );
  if (!preview || me === undefined) return <Spinner />;

  const here = `/invite/${token}`;
  const wrongAccount = me && me.email !== preview.email;
  return (
    <div className="vr-auth-box">
      <span className="vr-auth-icon">
        <Icon name="users" size={24} />
      </span>
      <div className="vr-auth-title">
        <span className="vr-eyebrow">Invitation</span>
        <h1 className="vr-display">Join {preview.workspace}</h1>
        <p className="vr-muted">
          {preview.invited_by} invited <b style={{ color: "var(--ink)" }}>{preview.email}</b> to join as {preview.role}.
        </p>
      </div>
      {f.error ? <Banner tone="danger">{f.error}</Banner> : null}
      {!me ? (
        <>
          <Link className="vr-btn vr-btn-primary vr-btn-lg" href={`/sign-up?next=${encodeURIComponent(here)}`}>
            Create an account
          </Link>
          <p className="vr-auth-foot vr-muted">
            Already have one? <Link className="vr-link" href={`/sign-in?next=${encodeURIComponent(here)}`}>Sign in</Link>
          </p>
        </>
      ) : wrongAccount ? (
        <Banner tone="warn" title={`You’re signed in as ${me.email}.`}>
          Sign out and sign in as {preview.email} to accept.
        </Banner>
      ) : !me.email_verified ? (
        <Banner tone="warn" title="Verify your email first.">Open the link we sent to {me.email}, then come back here.</Banner>
      ) : (
        <Button
          variant="primary"
          size="lg"
          busy={f.busy}
          onClick={() =>
            f.run(async () => {
              await api.post(`/invitations/${token}/accept`);
              router.push("/dashboard");
            })
          }
        >
          Accept and open workspace
        </Button>
      )}
    </div>
  );
}
