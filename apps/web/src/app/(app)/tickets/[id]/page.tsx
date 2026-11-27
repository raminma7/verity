"use client";

import { t as tr } from "@/i18n";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useEffect, useState } from "react";

import { EventLine } from "@/components/chat";
import { EmailCard } from "@/components/email";
import { PriorityBadge, StatusBadge } from "@/components/tickets";
import { Banner, Button, Card, dateTime, Icon, Segmented, Select, Spinner } from "@/components/ui";
import { api, errorText, type ChatMessage, type Team, type TicketCategory, type TicketDetail, type TicketEmail, type TicketEvent } from "@/lib/api";
import { PRIORITY, PRIORITY_ORDER, SOURCE, STATUS, STATUS_ORDER, showValue } from "@/lib/tickets";
import { useData } from "@/lib/use-data";

// Where each status may go; mirrors the API's transition table so only valid choices are offered.
const NEXT: Record<string, string[]> = {
  new: ["new", "open", "pending", "resolved", "closed"],
  open: ["open", "pending", "resolved", "closed"],
  pending: ["pending", "open", "resolved", "closed"],
  resolved: ["resolved", "open", "closed"],
  closed: ["closed", "open"],
};

export default function TicketPage() {
  const { id } = useParams<{ id: string }>();
  const data = useData<TicketDetail>(`/tickets/${id}`);
  const cats = useData<TicketCategory[]>("/ticket-categories?include_archived=true");
  const people = useData<{ id: string; name: string }[]>("/tickets/assignees");
  const teams = useData<{ items: Team[] }>("/teams");
  const [mode, setMode] = useState<"reply" | "note">("note");
  const [text, setText] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const { reload } = data;

  useEffect(() => {
    if (data.data?.can_reply) setMode("reply");
  }, [data.data?.can_reply]);
  useEffect(() => {
    const t = setInterval(reload, 10000);
    return () => clearInterval(t);
  }, [reload]);

  if (data.loading && !data.data) return <Spinner />;
  if (data.error || !data.data) return <Banner tone="danger">{data.error?.message ?? tr("Not found.")}</Banner>;
  const { ticket: t, form, events, messages, emails, reply_channel, can_manage, can_reply } = data.data;
  const byEmail = reply_channel?.kind === "email";
  const emailById = new Map(emails.map((m) => [m.id, m]));
  const linked = new Set(events.map((e) => String(e.data.email_message_id ?? "")).filter(Boolean));
  const firstIn = emails.find((m) => m.direction === "in");
  // The timeline: events, with each email shown where its event is; emails without an event (the first
  // message, automatic replies, confirmations) take their place by time.
  type Item = { at: string; event?: TicketEvent; email?: TicketEmail };
  const items: Item[] = [
    ...events.map((e) => ({ at: e.created_at, event: e })),
    ...emails.filter((m) => !linked.has(m.id)).map((m) => ({ at: m.created_at, email: m })),
  ].sort((a, b) => a.at.localeCompare(b.at));
  async function autoAssign() {
    setBusy("route");
    setErr(null);
    try {
      await api.post(`/tickets/${id}/route`);
      await reload();
    } catch (e) {
      setErr(errorText(e));
    } finally {
      setBusy(null);
    }
  }
  async function retry(emailId: string) {
    setBusy(`retry-${emailId}`);
    setErr(null);
    try {
      await api.post(`/tickets/${id}/emails/${emailId}/retry`);
      await reload();
    } catch (e) {
      setErr(errorText(e));
    } finally {
      setBusy(null);
    }
  }
  const catName = (cid: string | number | null | undefined) => cats.data?.find((c) => c.id === cid)?.name ?? "No category";
  const personName = (pid: string | number | null | undefined) => (pid ? people.data?.find((p) => p.id === pid)?.name ?? "a former member" : "nobody");

  async function change(patch: Record<string, unknown>) {
    setBusy("props");
    setErr(null);
    try {
      await api.patch(`/tickets/${id}`, patch);
      await reload();
    } catch (e) {
      setErr(errorText(e));
    } finally {
      setBusy(null);
    }
  }

  async function send(e: React.FormEvent) {
    e.preventDefault();
    if (!text.trim()) return;
    setBusy("send");
    setErr(null);
    try {
      await api.post(`/tickets/${id}/${mode === "reply" ? "reply" : "notes"}`, { text });
      setText("");
      await reload();
    } catch (e2) {
      setErr(errorText(e2));
    } finally {
      setBusy(null);
    }
  }

  return (
    <>
      <div>
        <Link className="vr-link" href="/tickets" style={{ fontSize: 13 }}>
          {tr("← Tickets")}
        </Link>
      </div>
      <header style={{ display: "flex", alignItems: "flex-start", gap: 12, flexWrap: "wrap" }}>
        <div style={{ flex: 1, minWidth: 0, display: "flex", flexDirection: "column", gap: 4 }}>
          <span className="vr-eyebrow">
            <span className="vr-mono">#{t.number}</span> · {tr(SOURCE[t.source]) ?? t.source} {tr("· opened")} {dateTime(t.created_at)}
          </span>
          <h1 className="vr-h" style={{ margin: 0 }}>
            {t.subject}
          </h1>
        </div>
        <PriorityBadge priority={t.priority} />
        <StatusBadge status={t.status} />
      </header>
      {err ? <Banner tone="danger">{err}</Banner> : null}
      <div className="vr-ticket">
        <section className="vr-timeline" aria-label={tr("Timeline")}>
          {t.description && !(t.source === "email" && firstIn) ? (
            <div className="vr-tl-item">
              <span className="vr-tl-meta">
                <b>{tr("Description")}</b>
              </span>
              <p>{t.description}</p>
            </div>
          ) : null}
          {items.map((it) => {
            const em = it.email ?? (it.event?.data.email_message_id ? emailById.get(String(it.event.data.email_message_id)) : undefined);
            if (em)
              return (
                <EmailCard
                  key={it.event?.id ?? em.id}
                  m={em}
                  author={it.event?.author}
                  onRetry={can_manage ? () => retry(em.id) : undefined}
                  retrying={busy === `retry-${em.id}`}
                />
              );
            return it.event ? <TimelineItem key={it.event.id} e={it.event} catName={catName} personName={personName} formName={form?.name ?? null} /> : null;
          })}
          {can_manage ? (
            <form className="vr-card vr-panel-body vr-composer" onSubmit={send}>
              <Segmented
                label={tr("Write")}
                value={mode}
                onChange={(v) => setMode(v as "reply" | "note")}
                options={[...(can_reply ? [{ value: "reply", label: byEmail ? tr("Reply by email") : tr("Reply in the chat") }] : []), { value: "note", label: tr("Internal note") }]}
              />
              <span className="vr-input vr-textarea">
                <textarea
                  rows={4}
                  value={text}
                  aria-label={mode === "reply" ? tr("Reply to the customer") : tr("Internal note")}
                  placeholder={
                    mode === "reply"
                      ? byEmail
                        ? tr("Write to the customer. It goes out by email, in their original thread.")
                        : tr("Write to the customer. It appears in their chat within a few seconds.")
                      : tr("Only your team sees internal notes.")
                  }
                  onChange={(e) => setText(e.target.value)}
                />
              </span>
              <div className="vr-actions" style={{ justifyContent: "space-between" }}>
                <span className="vr-help">
                  {mode === "reply"
                    ? reply_channel?.kind === "email"
                      ? tr("To {to}{v}.", { to: reply_channel.to, v: reply_channel.from ? `, from ${reply_channel.from}` : ", from Verity’s no-reply address (no mailbox connected)" })
                      : reply_channel?.kind === "chat" && reply_channel.email
                        ? tr("Replying takes the chat over: the assistant stays quiet until the ticket is resolved. Also emailed to {email}.", { email: reply_channel.email })
                        : tr("Replying takes the chat over: the assistant stays quiet until the ticket is resolved.")
                    : can_reply
                      ? tr("The customer never sees internal notes.")
                      : tr("This ticket has no chat or email address to reply to.")}
                </span>
                <Button type="submit" variant="primary" busy={busy === "send"} disabled={!text.trim() || (mode === "reply" && t.status === "closed")}>
                  {mode === "reply" ? tr("Send reply") : tr("Add note")}
                </Button>
              </div>
            </form>
          ) : (
            <p className="vr-muted" style={{ fontSize: 13 }}>
              {tr("Your role can read tickets but not change them.")}
            </p>
          )}
        </section>
        <aside className="vr-props" aria-label={tr("Ticket details")}>
          <Card title={tr("Properties")}>
            <div className="vr-form">
              <Select
                label={tr("Status")}
                value={t.status}
                disabled={!can_manage || busy === "props"}
                onChange={(v) => change({ status: v })}
                options={STATUS_ORDER.filter((s) => NEXT[t.status].includes(s)).map((s) => ({ value: s, label: tr(STATUS[s].label) }))}
              />
              <Select
                label={tr("Priority")}
                value={t.priority}
                disabled={!can_manage || busy === "props"}
                onChange={(v) => change({ priority: v })}
                options={PRIORITY_ORDER.map((p) => ({ value: p, label: tr(PRIORITY[p].label) }))}
              />
              <Select
                label={tr("Category")}
                value={t.category_id ?? ""}
                disabled={!can_manage || busy === "props"}
                onChange={(v) => change({ category_id: v || null })}
                options={[
                  { value: "", label: tr("No category") },
                  ...(cats.data ?? []).filter((c) => !c.archived || c.id === t.category_id).map((c) => ({ value: c.id, label: c.archived ? tr("{name} (archived)", { name: c.name }) : c.name })),
                ]}
              />
              <Select
                label={tr("Assignee")}
                value={t.assignee_id ?? ""}
                disabled={!can_manage || busy === "props"}
                onChange={(v) => change(v ? { assignee_id: v } : { unassign: true })}
                options={[{ value: "", label: tr("Unassigned") }, ...(people.data ?? []).map((p) => ({ value: p.id, label: p.name }))]}
              />
              <Select
                label={tr("Team")}
                value={t.team_id ?? ""}
                disabled={!can_manage || busy === "props"}
                onChange={(v) => change({ team_id: v || null })}
                options={[{ value: "", label: tr("No team") }, ...(teams.data?.items ?? []).map((x) => ({ value: x.id, label: x.name }))]}
              />
              {can_manage && !t.assignee_id ? (
                <Button size="sm" icon="users" busy={busy === "route"} onClick={autoAssign}>
                  {tr("Auto-assign")}
                </Button>
              ) : null}
              {t.first_response_at ? <span className="vr-help">{tr("First response")} {dateTime(t.first_response_at)}</span> : null}
            </div>
          </Card>
          <Card title={tr("Requester")}>
            <dl className="vr-kv" style={{ gridTemplateColumns: "90px 1fr" }}>
              <dt>{tr("Name")}</dt>
              <dd>{t.requester_name || "—"}</dd>
              <dt>{tr("Email")}</dt>
              <dd style={{ overflowWrap: "anywhere" }}>{t.requester_email || "—"}</dd>
              {t.reopened_count ? (
                <>
                  <dt>{tr("Reopened")}</dt>
                  <dd>{t.reopened_count}×</dd>
                </>
              ) : null}
            </dl>
          </Card>
          {form ? (
            <Card title={form.name ? tr("Submitted form · {name}", { name: form.name }) : tr("Submitted form")}>
              <dl className="vr-kv" style={{ gridTemplateColumns: "1fr" }} aria-label={tr("Submitted form data")}>
                {form.fields.map((f) => (
                  <div key={f.key} style={{ display: "contents" }}>
                    <dt>{f.label}</dt>
                    <dd className={f.type === "number" ? "vr-mono" : undefined} style={{ marginBottom: 8, overflowWrap: "anywhere", whiteSpace: "pre-line" }}>
                      {showValue(f.value)}
                    </dd>
                  </div>
                ))}
              </dl>
              {form.version ? <p className="vr-help">{tr("Form version")} {form.version}{tr(", as the customer saw it.")}</p> : null}
            </Card>
          ) : null}
          {data.data.conversation ? (
            <Card
              title={tr("Chat conversation")}
              actions={
                <Link className="vr-link" href={`/conversations/${data.data.conversation.id}`} style={{ fontSize: 13 }}>
                  {tr("Open →")}
                </Link>
              }
            >
              <div className="vr-convo" aria-label={tr("Conversation history")}>
                {messages.map((m) => (
                  <ConvoLine key={m.id} m={m} />
                ))}
              </div>
            </Card>
          ) : null}
        </aside>
      </div>
    </>
  );
}

function TimelineItem({
  e,
  catName,
  personName,
  formName,
}: {
  e: TicketEvent;
  catName: (id: string | number | null | undefined) => string;
  personName: (id: string | number | null | undefined) => string;
  formName: string | null;
}) {
  const when = <span className="vr-mono">{dateTime(e.created_at)}</span>;
  if (e.kind === "customer_message")
    return (
      <div className="vr-tl-item is-customer">
        <span className="vr-tl-meta">
          <b>{tr("Customer")}</b> {e.data.channel === "email" ? tr("replied by email") : tr("wrote in the chat")} · {when}
        </span>
        <p>{e.body}</p>
      </div>
    );
  if (e.kind === "agent_reply")
    return (
      <div className="vr-tl-item is-agent">
        <span className="vr-tl-meta">
          <b>{e.author ?? tr("Team")}</b> {tr("replied in the chat ·")} {when}
        </span>
        <p>{e.body}</p>
      </div>
    );
  if (e.kind === "note")
    return (
      <div className="vr-tl-item is-note">
        <span className="vr-tl-meta">
          <Icon name="lock" size={14} />
          <b>{tr("Internal note")}</b> · {e.author ?? tr("Team")} · {when}
        </span>
        <p>{e.body}</p>
      </div>
    );
  const who = e.author ?? "Verity";
  const label = (s: unknown) => STATUS[s as keyof typeof STATUS]?.label ?? String(s);
  const text =
    e.kind === "created"
      ? e.data.source === "chatbot_form"
        ? `Opened from the chatbot form “${e.data.form ?? formName ?? "form"}”.`
        : e.data.source === "email"
          ? "Opened from an incoming email."
        : e.data.source === "conversation"
          ? `${who} turned a chat conversation into this ticket.`
          : `${who} opened this ticket.`
      : e.kind === "status"
        ? `${who} changed the status from ${label(e.data.from)} to ${label(e.data.to)}.`
        : e.kind === "priority"
          ? `${who} set the priority to ${PRIORITY[e.data.to as keyof typeof PRIORITY]?.label ?? e.data.to}.`
          : e.kind === "category"
            ? `${who} moved it to ${catName(e.data.to)}.`
            : e.kind === "assignment"
              ? e.data.to
                ? `${who} assigned it to ${personName(e.data.to)}.`
                : `${who} unassigned it.`
              : e.kind === "reopened"
                ? "Reopened: the customer replied."
                : e.kind === "routed"
                  ? e.data.assignee
                    ? `Routed to ${e.data.assignee}${e.data.team ? ` in ${e.data.team}` : ""} (${e.data.reason}).`
                    : e.data.team
                      ? `Routed to ${e.data.team}’s queue: ${e.data.reason}.`
                      : `Not routed: ${e.data.reason}.`
                  : e.kind === "team"
                    ? e.data.to
                      ? `${who} moved it to the team ${e.data.team ?? ""}.`
                      : `${who} removed it from its team.`
                    : e.kind;
  return (
    <div className="vr-event" role="note">
      <span>{text}</span>
      {when}
    </div>
  );
}

function ConvoLine({ m }: { m: ChatMessage }) {
  if (m.kind === "event") return <EventLine m={m} />;
  if (m.kind === "form")
    return (
      <div className="vr-wform-card is-done">
        <b>{m.data.form?.title ?? tr("Form")}</b>
        <span className="vr-wform-sub">
          {m.data.state === "submitted" ? tr("Sent · ticket #{ticket_number}", { ticket_number: m.data.ticket_number }) : m.data.state === "dismissed" ? tr("Closed by the customer") : tr("Offered, not sent yet")}
        </span>
      </div>
    );
  if (m.role === "visitor")
    return (
      <div className="vr-bubble">
        {m.data.channel === "email" ? <span className="vr-bubble-tag">{tr("By email")}</span> : null}
        {m.content}
      </div>
    );
  if (m.role === "agent")
    return (
      <div className="vr-agent-msg">
        <span className="vr-eyebrow">
          {m.author ?? tr("Team")}
          {m.data.emailed_to ? tr(" · also emailed to {emailed_to}", { emailed_to: m.data.emailed_to }) : ""}
        </span>
        <p style={{ margin: 0, whiteSpace: "pre-line" }}>{m.content}</p>
      </div>
    );
  return (
    <div className="vr-wmsg is-bot" style={{ maxWidth: "100%" }}>
      {m.content}
    </div>
  );
}
