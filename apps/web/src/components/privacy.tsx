"use client";

/* Data retention, exports and deletion (Phase 6). Built from the design system's tokens and rules, like the
   other app-level sections: destructive actions are red, need a password, and say what happens and when. */

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { Badge, Banner, Button, Card, date, dateTime, Field, Select, Spinner } from "@/components/ui";
import { msg, t, tp } from "@/i18n";
import { api, ApiError, errorText } from "@/lib/api";
import { useSession } from "@/lib/session";
import { useData } from "@/lib/use-data";

type Retention = {
  retention: Record<string, number | null>;
  caps: Record<string, number | null>;
  effective: Record<string, number | null>;
  kinds: { key: string; label: string }[];
  can_edit: boolean;
};

const KIND_LABEL: Record<string, string> = {
  closed_tickets: msg("Closed tickets, with their timeline and email"),
  resolved_conversations: msg("Resolved chatbot conversations"),
  email_content: msg("Email bodies and attachments"),
  spam: msg("Mail held in Spam"),
};
const CHOICES = [null, 30, 90, 180, 365, 730, 1825];

function period(days: number | null): string {
  if (days === null) return t("Forever");
  if (days % 365 === 0) return tp("{n} year", "{n} years", days / 365);
  return tp("{n} day", "{n} days", days);
}

export function RetentionCard() {
  const d = useData<Retention>("/workspace/retention");
  const [form, setForm] = useState<Record<string, number | null> | null>(null);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<{ tone: "brand" | "danger"; text: string } | null>(null);
  if (d.loading && !d.data) return <Spinner />;
  if (d.error) return <Banner tone="danger">{d.error.message}</Banner>;
  const r = d.data!;
  const values = form ?? r.retention;
  return (
    <Card title={t("Data retention")}>
      <div className="vr-form">
        <p className="vr-muted" style={{ margin: 0 }}>
          {t("How long this workspace keeps data before it is erased for good. Erasing runs every hour and removes stored files too; ticket numbers are never reused.")}
        </p>
        {note ? <Banner tone={note.tone}>{note.text}</Banner> : null}
        <div className="vr-table-wrap">
          <table className="vr-table">
            <thead>
              <tr>
                <th>{t("Data")}</th>
                <th>{t("Keep for")}</th>
                <th>{t("Platform maximum")}</th>
              </tr>
            </thead>
            <tbody>
              {r.kinds.map((k) => {
                const cap = r.caps[k.key];
                return (
                  <tr key={k.key}>
                    <td>{t(KIND_LABEL[k.key] ?? k.label)}</td>
                    <td style={{ minWidth: 180 }}>
                      {r.can_edit ? (
                        <Select
                          ariaLabel={t(KIND_LABEL[k.key] ?? k.label)}
                          value={String(values[k.key] ?? "forever")}
                          onChange={(v) => setForm({ ...values, [k.key]: v === "forever" ? null : Number(v) })}
                          options={CHOICES.filter((c) => cap === null || (c !== null && c <= cap)).map((c) => ({ value: c === null ? "forever" : String(c), label: period(c) }))}
                        />
                      ) : (
                        period(r.effective[k.key])
                      )}
                    </td>
                    <td className="vr-muted">{cap === null ? t("None") : period(cap)}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
        {r.can_edit ? (
          <div className="vr-actions">
            <Button
              variant="primary"
              busy={busy}
              disabled={!form}
              onClick={async () => {
                setBusy(true);
                setNote(null);
                try {
                  await api.put("/workspace/retention", { retention: values });
                  await d.reload();
                  setForm(null);
                  setNote({ tone: "brand", text: t("Retention saved. Older data is erased within the hour.") });
                } catch (e) {
                  setNote({ tone: "danger", text: errorText(e) });
                } finally {
                  setBusy(false);
                }
              }}
            >
              {t("Save retention")}
            </Button>
          </div>
        ) : null}
      </div>
    </Card>
  );
}

type Export = { id: string; kind: string; status: string; size: number; created_at: string; expires_at: string | null; workspace_id: string | null };
const EXPORT_STATUS: Record<string, { tone: "brand" | "neutral" | "warn" | "danger"; word: string }> = {
  queued: { tone: "warn", word: msg("Being prepared") },
  ready: { tone: "brand", word: msg("Ready · link emailed") },
  downloaded: { tone: "neutral", word: msg("Downloaded") },
  expired: { tone: "neutral", word: msg("Expired") },
  failed: { tone: "danger", word: msg("Failed") },
};

export function ExportCard({ kind }: { kind: "workspace" | "personal" }) {
  const list = useData<Export[]>("/exports");
  const { current } = useSession();
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<{ tone: "brand" | "danger"; text: string } | null>(null);
  const mine = (list.data ?? []).filter((e) => e.kind === kind && (kind === "personal" || e.workspace_id === current?.workspace.id));
  return (
    <Card title={kind === "workspace" ? t("Export workspace data") : t("Export my data")}>
      <div className="vr-form">
        <p className="vr-muted" style={{ margin: 0 }}>
          {kind === "workspace"
            ? t("A ZIP of everything in this workspace: tickets, conversations, forms, email, members, settings, usage and invoices as JSON, plus stored files. We email you a link that works once, for 7 days.")
            : t("A ZIP of your own data: profile, workspaces, sign-ins, and the notes and replies you wrote. We email you a link that works once, for 7 days.")}
        </p>
        {note ? <Banner tone={note.tone}>{note.text}</Banner> : null}
        <div className="vr-actions">
          <Button
            busy={busy}
            icon="file"
            onClick={async () => {
              setBusy(true);
              setNote(null);
              try {
                await api.post(kind === "workspace" ? "/workspace/exports" : "/me/exports");
                await list.reload();
                setNote({ tone: "brand", text: t("Export requested. We’ll email you when it’s ready.") });
              } catch (e) {
                setNote({ tone: "danger", text: errorText(e) });
              } finally {
                setBusy(false);
              }
            }}
          >
            {kind === "workspace" ? t("Export workspace data") : t("Export my data")}
          </Button>
        </div>
        {mine.length ? (
          <ul className="vr-export-list">
            {mine.slice(0, 5).map((e) => {
              const s = EXPORT_STATUS[e.status] ?? EXPORT_STATUS.queued;
              return (
                <li key={e.id}>
                  <span>{dateTime(e.created_at)}</span>
                  <Badge tone={s.tone} dot>
                    {t(s.word)}
                  </Badge>
                  {e.expires_at && e.status === "ready" ? <span className="vr-muted">{t("until {date}", { date: date(e.expires_at) })}</span> : null}
                </li>
              );
            })}
          </ul>
        ) : null}
      </div>
    </Card>
  );
}

export function DeleteWorkspaceCard() {
  const { current, refresh } = useSession();
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [password, setPassword] = useState("");
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  if (!current) return null;
  const wsName = current.workspace.name;
  return (
    <Card title={t("Delete this workspace")} className="vr-danger-zone">
      <div className="vr-form">
        <p className="vr-muted" style={{ margin: 0 }}>
          {t("Closes the workspace for everyone at once and ends its subscription now, without a refund. You can restore it for 30 days; after that its tickets, conversations, email, knowledge base and files are erased for good. Invoices are kept for accounting, without your workspace's name.")}
        </p>
        {!open ? (
          <div className="vr-actions">
            <Button variant="danger" onClick={() => setOpen(true)}>
              {t("Delete workspace…")}
            </Button>
          </div>
        ) : (
          <form
            className="vr-form"
            style={{ maxWidth: 480 }}
            onSubmit={async (e) => {
              e.preventDefault();
              setBusy(true);
              setError(null);
              try {
                await api.post("/workspace/delete", { password, confirm_name: name });
                await refresh();
                router.push("/dashboard");
              } catch (err) {
                setError(errorText(err));
                setBusy(false);
              }
            }}
          >
            {error ? <Banner tone="danger">{error}</Banner> : null}
            <Field label={t("Type the workspace name: {name}", { name: wsName })} value={name} onChange={(e) => setName(e.target.value)} autoComplete="off" />
            <Field label={t("Your password")} type="password" value={password} onChange={(e) => setPassword(e.target.value)} autoComplete="current-password" />
            <div className="vr-actions">
              <Button type="submit" variant="danger" busy={busy} disabled={name !== wsName || !password}>
                {t("Delete {name}", { name: wsName })}
              </Button>
              <Button variant="ghost" onClick={() => setOpen(false)}>
                {t("Cancel")}
              </Button>
            </div>
          </form>
        )}
      </div>
    </Card>
  );
}

export function DeleteAccountCard() {
  const [open, setOpen] = useState(false);
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<{ text: string; workspaces?: string[] } | null>(null);
  return (
    <Card title={t("Delete my account")} className="vr-danger-zone">
      <div className="vr-form">
        <p className="vr-muted" style={{ margin: 0 }}>
          {t("Signs you out everywhere. Sign in again within 30 days to keep your account; after that your name and email are erased from Verity. Workspaces only you use are deleted with it; for workspaces others use, transfer ownership first.")}
        </p>
        {!open ? (
          <div className="vr-actions">
            <Button variant="danger" onClick={() => setOpen(true)}>
              {t("Delete my account…")}
            </Button>
          </div>
        ) : (
          <form
            className="vr-form"
            style={{ maxWidth: 420 }}
            onSubmit={async (e) => {
              e.preventDefault();
              setBusy(true);
              setError(null);
              try {
                await api.post("/me/delete", { password });
                window.location.assign("/sign-in?deleted=1");
              } catch (err) {
                const ws = err instanceof ApiError ? (err.data.workspaces as string[] | undefined) : undefined;
                setError({ text: errorText(err), workspaces: ws });
                setBusy(false);
              }
            }}
          >
            {error ? (
              <Banner tone="danger">
                {error.text}
                {error.workspaces?.length ? ` ${error.workspaces.join(", ")}` : ""}
              </Banner>
            ) : null}
            <Field label={t("Your password")} type="password" value={password} onChange={(e) => setPassword(e.target.value)} autoComplete="current-password" />
            <div className="vr-actions">
              <Button type="submit" variant="danger" busy={busy} disabled={!password}>
                {t("Delete my account")}
              </Button>
              <Button variant="ghost" onClick={() => setOpen(false)}>
                {t("Cancel")}
              </Button>
            </div>
          </form>
        )}
      </div>
    </Card>
  );
}

/** Shown in the app shell while a workspace of yours is scheduled for deletion. */
export function DeletionBanners() {
  const { workspaces, refresh } = useSession();
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [kept, setKept] = useState(false);
  useEffect(() => {
    try {
      if (sessionStorage.getItem("verity.notice") === "deletion_cancelled") {
        sessionStorage.removeItem("verity.notice");
        setKept(true);
      }
    } catch {
      /* private mode */
    }
  }, []);
  const scheduled = workspaces.filter((w) => w.deletion_requested_at);
  if (!scheduled.length && !kept) return null;
  return (
    <>
      {kept ? <Banner title={t("Welcome back. Your account won’t be deleted.")}>{t("Signing in cancelled the deletion you asked for.")}</Banner> : null}
      {error ? <Banner tone="danger">{error}</Banner> : null}
      {scheduled.map((w) => {
        const eraseOn = new Date(new Date(w.deletion_requested_at!).getTime() + 30 * 86400000).toISOString();
        return (
          <Banner
            key={w.id}
            tone="danger"
            title={t("{name} is scheduled for deletion.", { name: w.name })}
            action={
              w.is_owner ? (
                <Button
                  size="sm"
                  variant="secondary"
                  busy={busy === w.id}
                  onClick={async () => {
                    setBusy(w.id);
                    setError(null);
                    try {
                      await api.post(`/workspaces/${w.id}/restore`);
                      await refresh();
                    } catch (e) {
                      setError(errorText(e));
                    } finally {
                      setBusy(null);
                    }
                  }}
                >
                  {t("Restore workspace")}
                </Button>
              ) : null
            }
          >
            {w.is_owner
              ? t("It is closed for everyone and will be erased on {date}. Restore it before then to keep it; its subscription will need to be bought again.", { date: date(eraseOn) })
              : t("It is closed for everyone and will be erased on {date}. Only its owner can restore it.", { date: date(eraseOn) })}{" "}
            {w.is_owner ? null : <Link href="/settings/workspace?new=1">{t("Create a workspace")}</Link>}
          </Banner>
        );
      })}
    </>
  );
}
