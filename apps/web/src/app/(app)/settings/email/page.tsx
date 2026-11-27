"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { Banner, Button, Card, Field, Segmented, Select, Spinner, Switch } from "@/components/ui";
import { api, errorText, type AutoReply, type EmailAccounts, type EmailSettings } from "@/lib/api";
import { useSession } from "@/lib/session";
import { useData } from "@/lib/use-data";

const PLACEHOLDERS = [
  { key: "{ticket_number}", label: "Ticket number" },
  { key: "{customer_name}", label: "Customer’s first name" },
  { key: "{subject}", label: "Their subject" },
  { key: "{workspace}", label: "Workspace name" },
];

function preview(text: string, ws: string) {
  return text.replaceAll("{ticket_number}", "42").replaceAll("{customer_name}", "Jane").replaceAll("{subject}", "My order hasn’t arrived").replaceAll("{workspace}", ws);
}

function lines(v: string): string[] {
  return v
    .split(/[\n,]/)
    .map((s) => s.trim())
    .filter(Boolean);
}

export default function EmailSettingsPage() {
  const { can, current } = useSession();
  const manage = can("integrations.manage");
  const data = useData<EmailSettings>(manage ? "/email-settings" : null);
  const accounts = useData<EmailAccounts>(manage ? "/email-accounts" : null);
  const [reply, setReply] = useState<AutoReply | null>(null);
  const [allow, setAllow] = useState("");
  const [block, setBlock] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const [msg, setMsg] = useState<{ tone: "brand" | "danger"; text: string } | null>(null);
  useEffect(() => {
    if (data.data) {
      setReply(data.data.auto_reply);
      setAllow(data.data.allow_senders.join("\n"));
      setBlock(data.data.block_senders.join("\n"));
    }
  }, [data.data]);

  if (!manage) return <Banner tone="neutral">Your role can’t change email settings.</Banner>;
  if (!data.data || !reply) return data.error ? <Banner tone="danger">{data.error.message}</Banner> : <Spinner />;
  const s = data.data;
  const ws = current?.workspace.name ?? "your workspace";
  const boxes = (accounts.data?.items ?? []).filter((a) => a.status !== "disconnected");

  async function save(key: string, patch: Record<string, unknown>, ok = "Saved.") {
    setBusy(key);
    setMsg(null);
    try {
      data.setData(await api.patch<EmailSettings>("/email-settings", patch));
      setMsg({ tone: "brand", text: ok });
    } catch (e) {
      setMsg({ tone: "danger", text: errorText(e) });
    } finally {
      setBusy(null);
    }
  }

  return (
    <>
      {msg ? <Banner tone={msg.tone}>{msg.text}</Banner> : null}
      <Card title="Automatic first reply">
        <form
          className="vr-form"
          onSubmit={(e) => {
            e.preventDefault();
            save("reply", { auto_reply: reply }, "Automatic reply saved.");
          }}
        >
          <p className="vr-muted" style={{ margin: 0 }}>
            Sent once when a new email becomes a ticket, with its ticket number. Never to automatic messages, mailing lists or no-reply senders, and at most once per sender every ten minutes.
          </p>
          <Segmented
            label="Automatic reply"
            value={reply.mode}
            onChange={(v) => setReply({ ...reply, mode: v as AutoReply["mode"] })}
            options={[
              { value: "off", label: "Off" },
              { value: "template", label: "From a template" },
              { value: "ai", label: "Written by AI" },
            ]}
          />
          {reply.mode === "ai" ? (
            <Banner tone="neutral" title="An acknowledgement, in the customer’s language">
              The AI only thanks them, restates what they asked about and says the team will reply. It never answers, promises or links, and the ticket number is always included. It uses AI tokens; when they run out, the template below is sent instead.
            </Banner>
          ) : null}
          {reply.mode !== "off" ? (
            <>
              <Field label="Subject" value={reply.subject} onChange={(e) => setReply({ ...reply, subject: e.target.value })} maxLength={300} />
              <label className="vr-field">
                <span className="vr-field-top">{reply.mode === "ai" ? "Template (used when AI isn’t available)" : "Message"}</span>
                <span className="vr-input vr-textarea">
                  <textarea rows={7} value={reply.body} onChange={(e) => setReply({ ...reply, body: e.target.value })} maxLength={5000} aria-label="Automatic reply message" />
                </span>
                <span className="vr-help">Must include {"{ticket_number}"}.</span>
              </label>
              <div className="vr-actions" aria-label="Placeholders">
                {PLACEHOLDERS.map((p) => (
                  <button key={p.key} type="button" className="vr-chip" onClick={() => setReply({ ...reply, body: `${reply.body}${p.key}` })} title={p.label}>
                    <span className="vr-mono">{p.key}</span>
                  </button>
                ))}
              </div>
              <div className="vr-mailpreview" aria-label="Preview">
                <span className="vr-eyebrow">Preview</span>
                <b>{preview(reply.subject, ws)}</b>
                <p>{preview(reply.body, ws)}</p>
              </div>
            </>
          ) : null}
          <div className="vr-actions">
            <Button type="submit" variant="primary" busy={busy === "reply"}>
              Save automatic reply
            </Button>
            {reply.mode === "ai" ? (
              <Select label="Model" value={s.model} onChange={(v) => save("model", { model: v }, "Model saved.")} options={s.models.map((m) => ({ value: m, label: m }))} />
            ) : null}
          </div>
          <p className="vr-help" style={{ margin: 0 }}>
            A mailbox can override this on its own. Classification (spam and the ticket category) uses <span className="vr-mono">{s.model}</span>.
          </p>
        </form>
      </Card>
      <Card title="Email from the chat">
        <div className="vr-form">
          <Switch
            label="Confirm tickets opened from the chat by email"
            help="When a chatbot form is sent, or nobody replied in time and a ticket opens, the customer gets its number at the email they gave."
            checked={s.form_confirmation}
            disabled={busy === "confirm"}
            onChange={(v) => save("confirm", { form_confirmation: v })}
          />
          <Select
            label="Send chat replies and confirmations from"
            value={s.default_account_id ?? ""}
            onChange={(v) => save("default", { default_account_id: v || null })}
            options={[{ value: "", label: boxes.length ? "First connected mailbox" : "Verity’s no-reply address" }, ...boxes.map((a) => ({ value: a.id, label: a.address }))]}
            help={
              boxes.length ? (
                "Visitors’ replies to these emails come back into their conversation."
              ) : (
                <>
                  No mailbox is connected, so replies to Verity’s no-reply address aren’t read.{" "}
                  <Link className="vr-link" href="/email">
                    Connect one
                  </Link>
                </>
              )
            }
          />
        </div>
      </Card>
      <Card title="Spam and promotions">
        <form
          className="vr-form"
          onSubmit={(e) => {
            e.preventDefault();
            save("lists", { allow_senders: lines(allow), block_senders: lines(block) }, "Sender lists saved.");
          }}
        >
          <Switch
            label="Hold spam and promotions for review"
            help="Clear rules (spam flags, failed sender authentication, newsletters) decide first; the AI judges the rest. Off: every email becomes a ticket, except blocked senders."
            checked={s.spam_filter}
            disabled={busy === "filter"}
            onChange={(v) => save("filter", { spam_filter: v })}
          />
          <div className="vr-grid2">
            <label className="vr-field">
              <span className="vr-field-top">Always allow</span>
              <span className="vr-input vr-textarea">
                <textarea rows={4} value={allow} onChange={(e) => setAllow(e.target.value)} placeholder={"partner@example.com\n@trusted.example"} aria-label="Always allow" />
              </span>
              <span className="vr-help">One address or @domain per line. Their mail always becomes a ticket.</span>
            </label>
            <label className="vr-field">
              <span className="vr-field-top">Always block</span>
              <span className="vr-input vr-textarea">
                <textarea rows={4} value={block} onChange={(e) => setBlock(e.target.value)} placeholder="@spammy.example" aria-label="Always block" />
              </span>
              <span className="vr-help">Their mail goes straight to Spam.</span>
            </label>
          </div>
          <div className="vr-actions">
            <Button type="submit" variant="primary" busy={busy === "lists"}>
              Save sender lists
            </Button>
          </div>
        </form>
      </Card>
    </>
  );
}
