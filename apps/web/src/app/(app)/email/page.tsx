"use client";

import { t as tr, msg } from "@/i18n";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { AccountStatusBadge, accountState } from "@/components/email";
import { Badge, Banner, Button, Card, dateTime, EmptyState, Field, Segmented, Select, Spinner, UsageMeter } from "@/components/ui";
import { api, errorText, type EmailAccount, type EmailAccounts, type MailSecurity } from "@/lib/api";
import { useSession } from "@/lib/session";
import { useData } from "@/lib/use-data";

type Preset = { imap_host: string; imap_port: number; imap_security: MailSecurity; smtp_host: string; smtp_port: number; smtp_security: MailSecurity; note: string };

const PRESETS: Record<string, Preset> = {
  gmail: {
    imap_host: "imap.gmail.com",
    imap_port: 993,
    imap_security: "ssl",
    smtp_host: "smtp.gmail.com",
    smtp_port: 465,
    smtp_security: "ssl",
    note: msg("Turn on 2-Step Verification, then create an app password under Google Account → Security → App passwords."),
  },
  outlook: {
    imap_host: "outlook.office365.com",
    imap_port: 993,
    imap_security: "ssl",
    smtp_host: "smtp.office365.com",
    smtp_port: 587,
    smtp_security: "starttls",
    note: msg("Your Microsoft 365 admin must allow IMAP and SMTP AUTH for this mailbox; then create an app password in your security settings."),
  },
  fastmail: {
    imap_host: "imap.fastmail.com",
    imap_port: 993,
    imap_security: "ssl",
    smtp_host: "smtp.fastmail.com",
    smtp_port: 465,
    smtp_security: "ssl",
    note: msg("Create an app password under Settings → Privacy & Security → App passwords, with IMAP and SMTP access."),
  },
  other: { imap_host: "", imap_port: 993, imap_security: "ssl", smtp_host: "", smtp_port: 465, smtp_security: "ssl", note: msg("Your mail provider lists its IMAP and SMTP servers in its help pages.") },
};

const SECURITY = [
  { value: "ssl", label: msg("SSL/TLS") },
  { value: "starttls", label: msg("STARTTLS") },
];

const EMPTY = { name: "", address: "", display_name: "", kind: "organization", username: "", password: "", ...PRESETS.gmail };

export default function MailboxesPage() {
  const { can } = useSession();
  const router = useRouter();
  const manage = can("integrations.manage");
  useEffect(() => {
    if (!manage && can("email.review")) router.replace("/email/spam");
  }, [manage, can, router]);
  const data = useData<EmailAccounts>(manage ? "/email-accounts" : null);
  const [preset, setPreset] = useState("gmail");
  const [form, setForm] = useState({ ...EMPTY });
  const [busy, setBusy] = useState<string | null>(null);
  const [msg, setMsg] = useState<{ tone: "brand" | "danger"; text: string } | null>(null);
  const [connectErr, setConnectErr] = useState<string | null>(null);
  const [pw, setPw] = useState<{ id: string; value: string } | null>(null);
  const [confirm, setConfirm] = useState<string | null>(null);
  const { reload } = data;
  const busyFetch = data.data?.items.some((a) => a.fetch_status === "fetching" || a.fetch_status === "queued");
  useEffect(() => {
    const t = setInterval(reload, busyFetch ? 3000 : 20000);
    return () => clearInterval(t);
  }, [reload, busyFetch]);

  if (!manage) return can("email.review") ? <Spinner /> : <Banner tone="neutral">{tr("Your role can’t manage mailboxes.")}</Banner>;
  if (data.loading && !data.data) return <Spinner />;
  if (data.error || !data.data) return <Banner tone="danger">{data.error?.message ?? tr("Couldn’t load mailboxes.")}</Banner>;
  const { items, limit, used, default_account_id } = data.data;
  const full = limit >= 0 && used >= limit;

  async function act(key: string, fn: () => Promise<unknown>, ok?: string) {
    setBusy(key);
    setMsg(null);
    try {
      await fn();
      await reload();
      if (ok) setMsg({ tone: "brand", text: ok });
    } catch (e) {
      setMsg({ tone: "danger", text: errorText(e) });
    } finally {
      setBusy(null);
    }
  }

  async function connect(e: React.FormEvent) {
    e.preventDefault();
    setBusy("connect");
    setConnectErr(null);
    try {
      const a = await api.post<EmailAccount>("/email-accounts", { ...form, username: form.username || undefined });
      setForm({ ...EMPTY, ...PRESETS[preset] });
      await reload();
      setMsg({ tone: "brand", text: tr("{address} is connected. New mail arrives as tickets within a minute.", { address: a.address }) });
    } catch (e2) {
      setConnectErr(errorText(e2));
    } finally {
      setBusy(null);
    }
  }

  const set = (k: keyof typeof form) => (e: React.ChangeEvent<HTMLInputElement>) => setForm({ ...form, [k]: k.endsWith("_port") ? Number(e.target.value) : e.target.value });

  return (
    <>
      {msg ? <Banner tone={msg.tone}>{msg.text}</Banner> : null}
      <Card title={tr("Mailboxes")} actions={<span className="vr-num">{limit < 0 ? tr("{used} connected", { used: used }) : tr("{used} of {limit}", { used: used, limit: limit })}</span>}>
        <UsageMeter used={used} total={limit} label={tr("Connected mailboxes on your plan")} />
        {!items.length ? (
          <EmptyState icon="mail" title={tr("No mailbox connected yet")}>
            {tr("Connect your support address below. Verity reads new mail only, from the moment you connect: your mailbox’s history stays where it is.")}
          </EmptyState>
        ) : (
          <div className="vr-mailboxes" aria-label={tr("Connected mailboxes")}>
            {items.map((a) => {
              const st = accountState(a);
              return (
                <div key={a.id} className="vr-mailbox">
                  <div className="vr-mailbox-main">
                    <span className="vr-mailbox-name">
                      <b>{a.name}</b>
                      <span className="vr-mono vr-muted">{a.address}</span>
                    </span>
                    <span className="vr-actions">
                      <AccountStatusBadge account={a} />
                      <Badge>{a.kind === "personal" ? tr("Personal") : tr("Organization")}</Badge>
                      {default_account_id === a.id ? <Badge tone="brand">{tr("Sends chat and form email")}</Badge> : null}
                    </span>
                    <span className="vr-help">
                      {a.last_success_at ? tr("Last checked {last_success_at}", { last_success_at: dateTime(a.last_success_at) }) : tr("Not checked yet")} {tr("· IMAP")} {a.imap_host} {tr("· SMTP")} {a.smtp_host}
                    </span>
                    {a.last_error && (st === "auth" || st === "error") ? <span className="vr-help vr-help-error">{a.last_error}</span> : null}
                    {pw?.id === a.id ? (
                      <form
                        className="vr-actions"
                        style={{ alignItems: "flex-end", marginTop: 8 }}
                        onSubmit={(e) => {
                          e.preventDefault();
                          act(`pw-${a.id}`, () => api.patch(`/email-accounts/${a.id}`, { password: pw.value }), tr("App password updated. Verity signed in again.")).then(() => setPw(null));
                        }}
                      >
                        <Field label={tr("New app password")} type="password" autoComplete="new-password" value={pw.value} onChange={(e) => setPw({ id: a.id, value: e.target.value })} />
                        <Button type="submit" variant="primary" size="sm" busy={busy === `pw-${a.id}`} disabled={!pw.value}>
                          {tr("Save")}
                        </Button>
                        <Button size="sm" variant="ghost" onClick={() => setPw(null)}>
                          {tr("Cancel")}
                        </Button>
                      </form>
                    ) : null}
                  </div>
                  <span className="vr-actions vr-mailbox-actions">
                    <Button size="sm" icon="refresh" disabled={a.status !== "active"} busy={busy === `fetch-${a.id}`} onClick={() => act(`fetch-${a.id}`, () => api.post(`/email-accounts/${a.id}/fetch`))}>
                      {tr("Check now")}
                    </Button>
                    <Button size="sm" variant="ghost" onClick={() => setPw({ id: a.id, value: "" })}>
                      {tr("Update app password")}
                    </Button>
                    {a.status === "paused" ? (
                      <Button size="sm" variant="ghost" onClick={() => act(`st-${a.id}`, () => api.patch(`/email-accounts/${a.id}`, { status: "active" }), tr("{address} resumed.", { address: a.address }))}>
                        {tr("Resume")}
                      </Button>
                    ) : a.status === "active" ? (
                      <Button size="sm" variant="ghost" onClick={() => act(`st-${a.id}`, () => api.patch(`/email-accounts/${a.id}`, { status: "paused" }), tr("{address} paused. No mail is fetched or sent from it.", { address: a.address }))}>
                        {tr("Pause")}
                      </Button>
                    ) : null}
                    {confirm === a.id ? (
                      <>
                        <Button size="sm" variant="danger" busy={busy === `del-${a.id}`} onClick={() => act(`del-${a.id}`, () => api.del(`/email-accounts/${a.id}`), tr("{address} disconnected and its app password erased.", { address: a.address })).then(() => setConfirm(null))}>
                          {tr("Disconnect and erase password")}
                        </Button>
                        <Button size="sm" variant="ghost" onClick={() => setConfirm(null)}>
                          {tr("Keep")}
                        </Button>
                      </>
                    ) : (
                      <Button size="sm" variant="ghost" onClick={() => setConfirm(a.id)}>
                        {tr("Disconnect")}
                      </Button>
                    )}
                  </span>
                </div>
              );
            })}
          </div>
        )}
      </Card>
      <Card title={tr("Connect a mailbox")}>
        {full ? (
          <Banner tone="warn" title={tr("Your plan’s mailbox limit is reached")}>
            {tr("Disconnect a mailbox or upgrade your plan to connect another.")}
          </Banner>
        ) : (
          <form className="vr-form" onSubmit={connect} aria-label={tr("Connect a mailbox")}>
            <Segmented
              label={tr("Provider")}
              value={preset}
              onChange={(v) => {
                setPreset(v);
                setForm({ ...form, ...PRESETS[v] });
              }}
              options={[
                { value: "gmail", label: tr("Gmail") },
                { value: "outlook", label: tr("Outlook / 365") },
                { value: "fastmail", label: tr("Fastmail") },
                { value: "other", label: tr("Other") },
              ]}
            />
            <p className="vr-help" style={{ margin: 0 }}>
              {tr(PRESETS[preset].note)} {tr("Verity connects over TLS only and stores the app password encrypted; nobody can read it back, not even here.")}
            </p>
            <div className="vr-grid2">
              <Field label={tr("Email address")} type="email" required value={form.address} onChange={set("address")} placeholder={tr("support@yourcompany.com")} autoComplete="off" />
              <Field label={tr("Name in Verity")} value={form.name} onChange={set("name")} placeholder={tr("Support")} maxLength={80} />
              <Field label={tr("Sender name")} value={form.display_name} onChange={set("display_name")} placeholder={tr("Acme Support")} maxLength={120} help={tr("Customers see this as the sender.")} />
              <Field label={tr("Username")} value={form.username} onChange={set("username")} placeholder={tr("Same as the address")} autoComplete="off" help={tr("Only if it differs from the email address.")} />
              <Field label={tr("App password")} type="password" required value={form.password} onChange={set("password")} autoComplete="new-password" help={tr("Not your normal password: an app password made for Verity.")} />
              <div className="vr-field">
                <span className="vr-field-top">{tr("Mailbox type")}</span>
                <Segmented
                  label={tr("Mailbox type")}
                  value={form.kind}
                  onChange={(v) => setForm({ ...form, kind: v })}
                  options={[
                    { value: "organization", label: tr("Organization") },
                    { value: "personal", label: tr("Personal") },
                  ]}
                />
              </div>
            </div>
            <div className="vr-grid3">
              <Field label={tr("IMAP server (incoming)")} required value={form.imap_host} onChange={set("imap_host")} placeholder="imap.example.com" />
              <Field label={tr("Port")} type="number" required value={String(form.imap_port)} onChange={set("imap_port")} min={1} max={65535} />
              <Select label={tr("Security")} value={form.imap_security} onChange={(v) => setForm({ ...form, imap_security: v as MailSecurity })} options={SECURITY} />
              <Field label={tr("SMTP server (outgoing)")} required value={form.smtp_host} onChange={set("smtp_host")} placeholder="smtp.example.com" />
              <Field label={tr("Port")} type="number" required value={String(form.smtp_port)} onChange={set("smtp_port")} min={1} max={65535} />
              <Select label={tr("Security")} value={form.smtp_security} onChange={(v) => setForm({ ...form, smtp_security: v as MailSecurity })} options={SECURITY} />
            </div>
            {connectErr ? <Banner tone="danger">{connectErr}</Banner> : null}
            <div className="vr-actions">
              <Button type="submit" variant="primary" icon="mail" busy={busy === "connect"}>
                {tr("Sign in and connect")}
              </Button>
              <span className="vr-help">{tr("Verity signs in to both servers before saving anything.")}</span>
            </div>
          </form>
        )}
      </Card>
    </>
  );
}
