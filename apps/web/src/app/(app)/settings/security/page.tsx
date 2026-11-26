"use client";

import { useState } from "react";

import { Badge, Banner, Button, Card, dateTime, Field } from "@/components/ui";
import { api, errorText } from "@/lib/api";
import { useSession } from "@/lib/session";
import { useData } from "@/lib/use-data";

type Sess = { id: string; ip: string | null; user_agent: string | null; created_at: string; last_seen_at: string; current: boolean };

function browser(ua: string | null) {
  if (!ua) return "Unknown device";
  const b = /Edg\//.test(ua) ? "Edge" : /Chrome\//.test(ua) ? "Chrome" : /Firefox\//.test(ua) ? "Firefox" : /Safari\//.test(ua) ? "Safari" : "Browser";
  const os = /Windows/.test(ua) ? "Windows" : /Mac OS X/.test(ua) ? "macOS" : /Android/.test(ua) ? "Android" : /iPhone|iPad/.test(ua) ? "iOS" : /Linux/.test(ua) ? "Linux" : "";
  return os ? `${b} on ${os}` : b;
}

function PasswordCard() {
  const [cur, setCur] = useState("");
  const [next, setNext] = useState("");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<{ tone: "brand" | "danger"; text: string } | null>(null);
  return (
    <Card title="Password">
      <form
        className="vr-form"
        style={{ maxWidth: 520 }}
        onSubmit={async (e) => {
          e.preventDefault();
          setBusy(true);
          setMsg(null);
          try {
            await api.post("/me/password", { current_password: cur, new_password: next });
            setCur("");
            setNext("");
            setMsg({ tone: "brand", text: "Password changed. Other devices were signed out." });
          } catch (err) {
            setMsg({ tone: "danger", text: errorText(err) });
          } finally {
            setBusy(false);
          }
        }}
      >
        {msg ? <Banner tone={msg.tone}>{msg.text}</Banner> : null}
        <Field label="Current password" type="password" autoComplete="current-password" value={cur} onChange={(e) => setCur(e.target.value)} />
        <Field label="New password" type="password" autoComplete="new-password" value={next} onChange={(e) => setNext(e.target.value)} help="At least 10 characters, with upper and lower case letters and a number." />
        <div>
          <Button type="submit" variant="primary" busy={busy} disabled={!cur || !next}>
            Change password
          </Button>
        </div>
      </form>
    </Card>
  );
}

function TwoFactorCard() {
  const { user, refresh } = useSession();
  const [setup, setSetup] = useState<{ secret: string; otpauth_uri: string } | null>(null);
  const [codes, setCodes] = useState<string[] | null>(null);
  const [code, setCode] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function run(fn: () => Promise<void>) {
    setBusy(true);
    setError(null);
    try {
      await fn();
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card title="Two-factor authentication" actions={user.totp_enabled ? <Badge tone="brand" dot>On</Badge> : <Badge>Off</Badge>}>
      <div className="vr-form" style={{ maxWidth: 560 }}>
        {error ? <Banner tone="danger">{error}</Banner> : null}
        {codes ? (
          <>
            <Banner title="Two-factor authentication is on.">Save these recovery codes somewhere safe. Each works once if you lose your device. They won’t be shown again.</Banner>
            <div className="vr-codes">
              {codes.map((c) => (
                <span key={c}>{c}</span>
              ))}
            </div>
            <div className="vr-actions">
              <Button icon="copy" onClick={() => navigator.clipboard?.writeText(codes.join("\n"))}>
                Copy codes
              </Button>
              <Button variant="primary" onClick={() => setCodes(null)}>
                I’ve saved them
              </Button>
            </div>
          </>
        ) : user.totp_enabled ? (
          <>
            <p className="vr-muted">To turn it off, confirm your password and a current code.</p>
            <div className="vr-form-row">
              <Field label="Password" type="password" value={password} onChange={(e) => setPassword(e.target.value)} />
              <Field label="Code" inputMode="numeric" maxLength={6} value={code} onChange={(e) => setCode(e.target.value.replace(/\D/g, ""))} />
            </div>
            <div>
              <Button variant="danger" busy={busy} disabled={!password || code.length !== 6} onClick={() => run(async () => { await api.post("/me/2fa/disable", { password, code }); setPassword(""); setCode(""); await refresh(); })}>
                Turn off two-factor
              </Button>
            </div>
          </>
        ) : setup ? (
          <>
            <p className="vr-muted">
              Add Verity to your authenticator app (1Password, Google Authenticator, Authy…) with this key, then enter the 6-digit code it shows.
            </p>
            <div className="vr-codes" style={{ gridTemplateColumns: "1fr" }}>
              <span style={{ wordBreak: "break-all" }}>{setup.secret.match(/.{1,4}/g)?.join(" ")}</span>
            </div>
            <a className="vr-link" href={setup.otpauth_uri} style={{ fontSize: 13 }}>
              Open in authenticator app
            </a>
            <Field label="Code from the app" inputMode="numeric" autoComplete="one-time-code" maxLength={6} value={code} onChange={(e) => setCode(e.target.value.replace(/\D/g, ""))} />
            <div className="vr-actions">
              <Button variant="primary" busy={busy} disabled={code.length !== 6} onClick={() => run(async () => { const r = await api.post<{ recovery_codes: string[] }>("/me/2fa/enable", { code }); setCodes(r.recovery_codes); setSetup(null); setCode(""); await refresh(); })}>
                Turn on
              </Button>
              <Button variant="ghost" onClick={() => setSetup(null)}>
                Cancel
              </Button>
            </div>
          </>
        ) : (
          <>
            <p className="vr-muted">Ask for a code from an authenticator app each time you sign in, so a stolen password isn’t enough.</p>
            <div>
              <Button icon="shield" busy={busy} onClick={() => run(async () => setSetup(await api.post("/me/2fa/setup")))}>
                Set up two-factor
              </Button>
            </div>
          </>
        )}
      </div>
    </Card>
  );
}

function SessionsCard() {
  const s = useData<Sess[]>("/me/sessions");
  return (
    <Card title="Signed-in devices" padded={false}>
      <div className="vr-table-wrap">
        <table className="vr-table">
          <thead>
            <tr>
              <th>Device</th>
              <th>IP address</th>
              <th>Last active</th>
              <th><span className="sr-only">Actions</span></th>
            </tr>
          </thead>
          <tbody>
            {(s.data ?? []).map((x) => (
              <tr key={x.id}>
                <td>
                  {browser(x.user_agent)} {x.current ? <Badge tone="brand">This device</Badge> : null}
                </td>
                <td className="vr-mono" style={{ fontSize: 13 }}>
                  {x.ip ?? "—"}
                </td>
                <td className="vr-muted">{dateTime(x.last_seen_at)}</td>
                <td style={{ textAlign: "right" }}>
                  {!x.current ? (
                    <Button variant="ghost" size="sm" onClick={async () => { await api.del(`/me/sessions/${x.id}`); s.reload(); }}>
                      Sign out
                    </Button>
                  ) : null}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Card>
  );
}

export default function Security() {
  return (
    <>
      <PasswordCard />
      <TwoFactorCard />
      <SessionsCard />
    </>
  );
}
