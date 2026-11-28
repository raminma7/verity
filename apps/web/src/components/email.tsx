"use client";

import { t, msg } from "@/i18n";
/**
 * Email pieces (Phase 4), built only from the design system's tokens and rules: status always carries a word,
 * teal acts, lime stays for evidence, and cards sit on a line border.
 *
 * Email bodies are untrusted. The API sanitizes HTML before it is stored; on top of that, formatted mail is
 * shown only inside a sandboxed iframe with no scripts, no same-origin access and a CSP that loads nothing.
 */
import { useState } from "react";

import type { EmailAccount, EmailAttachmentInfo, HeldEmail, TicketEmail } from "@/lib/api";

import { FileTag } from "./rag";
import { Badge, Button, cx, dateTime, Icon, type Tone } from "./ui";

export const ACCOUNT_STATUS: Record<string, { label: string; tone: Tone }> = {
  connected: { label: msg("Connected"), tone: "brand" },
  fetching: { label: msg("Checking for mail…"), tone: "warn" },
  queued: { label: msg("Checking soon"), tone: "neutral" },
  paused: { label: msg("Paused"), tone: "neutral" },
  auth: { label: msg("Sign-in failed"), tone: "danger" },
  error: { label: msg("Can’t reach the server"), tone: "danger" },
  disconnected: { label: msg("Disconnected"), tone: "neutral" },
};

export function accountState(a: EmailAccount): keyof typeof ACCOUNT_STATUS {
  if (a.status === "disconnected") return "disconnected";
  if (a.status === "paused") return "paused";
  if (a.status === "error" || a.last_error_code === "email_auth_failed") return "auth";
  if (a.fetch_status === "fetching") return "fetching";
  if (a.fetch_status === "queued") return "queued";
  if (a.fetch_status === "error") return "error";
  return "connected";
}

export function AccountStatusBadge({ account }: { account: EmailAccount }) {
  const s = ACCOUNT_STATUS[accountState(account)];
  return (
    <Badge tone={s.tone} dot>
      {t(s.label)}
    </Badge>
  );
}

export const HELD_REASON: Record<string, { label: string; tone: Tone }> = {
  spam: { label: msg("Spam"), tone: "danger" },
  promotion: { label: msg("Promotion"), tone: "warn" },
  rate_limited: { label: msg("Too many from this sender"), tone: "neutral" },
};

const RULE_WORDS: Record<string, string> = {
  spam_flag_header: msg("the receiving server flagged it as spam"),
  authentication_failed: msg("the sender’s domain didn’t authenticate it (SPF and DMARC/DKIM failed)"),
  block_list: msg("the sender is on your block list"),
  list_unsubscribe: msg("it has an unsubscribe link"),
  list_id: msg("it was sent to a mailing list"),
  precedence_bulk: msg("it was sent as bulk mail"),
  sender_rate_limit: msg("this sender sent more new requests than the hourly limit"),
};

/** Why a message is held, in words: the rules that matched and who decided (a rule, the spam filter or the AI). */
export function heldReason(m: HeldEmail): string {
  const hits = (m.verdict.rules ?? []).map((r) =>
    RULE_WORDS[r] ? t(RULE_WORDS[r]) : r.startsWith("esp:") ? t("it came from a bulk-mail service") : r.startsWith("word:") ? t("it says “{word}”", { word: r.slice(5) }) : r,
  );
  const reasons = Array.from(new Set(hits)).join("; ");
  const spam = m.status === "spam";
  if (m.verdict.decided_by === "model" && m.verdict.model) {
    const percent = Math.round(m.verdict.model.p_spam * 100);
    return reasons ? t("The spam filter rated it {percent}% likely to be spam; also, {reasons}.", { percent, reasons }) : t("The spam filter rated it {percent}% likely to be spam.", { percent });
  }
  if (m.verdict.decided_by === "ai") {
    if (reasons) return spam ? t("The AI judged it spam; also, {reasons}.", { reasons }) : t("The AI judged it promotional; also, {reasons}.", { reasons });
    return spam ? t("The AI judged it spam.") : t("The AI judged it promotional.");
  }
  return reasons ? t("Held because {reasons}.", { reasons }) : t("Held by a rule.");
}

const BLOCKED: Record<string, string> = {
  blocked_type: msg("Not kept: this type of file can carry malware"),
  type_mismatch: msg("Not kept: its content doesn’t match its name"),
  too_large: msg("Not kept: larger than the limit"),
  too_many: msg("Not kept: too many attachments"),
  archive_too_large: msg("Not kept: the archive expands too far"),
  storage_full: msg("Not kept: your plan’s storage is full"),
};

function size(n: number) {
  return n < 1024 ? `${n} B` : n < 1024 * 1024 ? `${Math.round(n / 1024)} KB` : `${(n / 1024 / 1024).toFixed(1)} MB`;
}

export function AttachmentChip({ a }: { a: EmailAttachmentInfo }) {
  const ext = a.filename.includes(".") ? a.filename.split(".").pop() : "file";
  const inner = (
    <>
      <FileTag type={ext} />
      <span className="vr-att-name">{a.filename}</span>
      <span className="vr-num">{size(a.size)}</span>
    </>
  );
  if (!a.available)
    return (
      <span className="vr-att is-blocked" title={t(BLOCKED[a.blocked_reason ?? ""]) ?? t("Not kept")}>
        {inner}
        <span className="vr-help">{t(BLOCKED[a.blocked_reason ?? ""]) ?? t("Not kept")}</span>
      </span>
    );
  return (
    <a className="vr-att" href={`/api/email-attachments/${a.id}`} download={a.filename}>
      {inner}
      <Icon name="upload" size={14} className="vr-att-dl" />
    </a>
  );
}

const FRAME_CSS =
  "body{margin:0;padding:12px;font:14px/22px system-ui,sans-serif;color:#15171a;background:#fff;overflow-wrap:anywhere}" +
  "a{color:#0d6655}table{max-width:100%;border-collapse:collapse}td,th{padding:2px 4px;vertical-align:top}blockquote{margin:0 0 0 8px;padding-left:8px;border-left:2px solid #d9d5c9;color:#5b5f66}";

/** Formatted email, already sanitized by the API, inside a sandbox that runs nothing and loads nothing. */
export function SafeHtml({ html, title }: { html: string; title: string }) {
  const doc =
    `<!doctype html><html><head><meta charset="utf-8"><meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; img-src data:">` +
    `<base target="_blank"><style>${FRAME_CSS}</style></head><body>${html}</body></html>`;
  return <iframe className="vr-mailframe" title={title} sandbox="allow-popups allow-popups-to-escape-sandbox" srcDoc={doc} referrerPolicy="no-referrer" />;
}

/** The body of an email: plain text, with a toggle to the sanitized formatted version when there is one. */
export function MailBody({ text, html, title }: { text: string; html?: string | null; title: string }) {
  const [formatted, setFormatted] = useState(false);
  return (
    <div className="vr-mailbody">
      {formatted && html ? <SafeHtml html={html} title={title} /> : <p>{text || t("(no text)")}</p>}
      {html ? (
        <Button size="sm" variant="ghost" onClick={() => setFormatted(!formatted)}>
          {formatted ? t("Show plain text") : t("Show formatted")}
        </Button>
      ) : null}
    </div>
  );
}

const DELIVERY: Record<string, { label: string; tone: Tone }> = {
  queued: { label: msg("Sending…"), tone: "warn" },
  sent: { label: msg("Sent"), tone: "brand" },
  failed: { label: msg("Not sent"), tone: "danger" },
};

const OUT_KIND: Record<string, string> = {
  auto_reply: msg("Automatic first reply"),
  agent_reply: msg("replied by email"),
  chat_reply: msg("chat reply, also sent by email"),
  form_confirmation: msg("Confirmation email"),
};

/** One email of a ticket's thread: who wrote, to whom, the body, attachments and (for outgoing mail) delivery. */
export function EmailCard({ m, author, onRetry, retrying }: { m: TicketEmail; author?: string | null; onRetry?: () => void; retrying?: boolean }) {
  const out = m.direction === "out";
  const who = out ? (m.kind === "agent_reply" || m.kind === "chat_reply" ? author ?? t("Team") : t(OUT_KIND[m.kind ?? ""] ?? "Verity")) : m.from_name || m.from_addr;
  const d = DELIVERY[m.status];
  return (
    <article className={cx("vr-tl-item", "vr-mail", out ? "is-agent" : "is-customer")} aria-label={out ? t("Email sent") : t("Email received")}>
      <header className="vr-mail-head">
        <Icon name="mail" size={14} />
        <span className="vr-tl-meta">
          <b>{who}</b>
          {out && (m.kind === "agent_reply" || m.kind === "chat_reply") ? ` ${t(OUT_KIND[m.kind])}` : out ? "" : ` ${t("wrote by email")}`} ·{" "}
          <span className="vr-mono">{dateTime(m.sent_at ?? m.received_at ?? m.created_at)}</span>
        </span>
        {out && d ? (
          <Badge tone={d.tone} dot>
            {t(d.label)}
          </Badge>
        ) : null}
      </header>
      <dl className="vr-mail-addr">
        <dt>{t("From")}</dt>
        <dd>{out ? (m.via_platform ? t("Verity (no-reply)") : m.from_addr) : m.from_name ? `${m.from_name} <${m.from_addr}>` : m.from_addr}</dd>
        <dt>{t("To")}</dt>
        <dd>{m.to.join(", ")}</dd>
        {m.cc.length ? (
          <>
            <dt>{t("Cc")}</dt>
            <dd>{m.cc.join(", ")}</dd>
          </>
        ) : null}
        <dt>{t("Subject")}</dt>
        <dd>{m.subject || t("(no subject)")}</dd>
      </dl>
      <MailBody text={m.text} html={m.html} title={t("Email: {subject}", { subject: m.subject })} />
      {m.attachments.length ? (
        <div className="vr-atts" aria-label={t("Attachments")}>
          {m.attachments.map((a) => (
            <AttachmentChip key={a.id} a={a} />
          ))}
        </div>
      ) : null}
      {m.status === "failed" ? (
        <div className="vr-actions">
          <span className="vr-help vr-help-error">{m.error ?? t("The mail server didn’t accept this email.")}</span>
          {onRetry ? (
            <Button size="sm" icon="refresh" busy={retrying} onClick={onRetry}>
              {t("Retry")}
            </Button>
          ) : null}
        </div>
      ) : null}
      {out && m.via_platform && m.status !== "failed" ? <span className="vr-help">{t("Sent from Verity’s address because no mailbox is connected; replies to it aren’t read.")}</span> : null}
    </article>
  );
}
