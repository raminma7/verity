"use client";

import { t as tr } from "@/i18n";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useEffect, useRef, useState } from "react";

import { AssistantAnswer, EventLine, SourcesRail } from "@/components/chat";
import { Badge, Banner, Button, Card, dateTime, Field, Select, Spinner } from "@/components/ui";
import { api, errorText, type ChatMessage, type Conversation, type TicketCategory, type TicketStatus } from "@/lib/api";
import { PRIORITY, PRIORITY_ORDER, STATUS } from "@/lib/tickets";
import { useData } from "@/lib/use-data";

export default function ConversationPage() {
  const { id } = useParams<{ id: string }>();
  const data = useData<{
    conversation: Conversation;
    messages: ChatMessage[];
    can_reply: boolean;
    can_create_ticket?: boolean;
    tickets?: { id: string; number: number; status: TicketStatus }[];
  }>(`/conversations/${id}`);
  const [converting, setConverting] = useState(false);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [active, setActive] = useState<number | null>(null);
  const endRef = useRef<HTMLDivElement>(null);
  const { reload } = data;

  useEffect(() => {
    const t = setInterval(reload, 4000);
    return () => clearInterval(t);
  }, [reload]);
  const count = data.data?.messages.length ?? 0;
  useEffect(() => {
    endRef.current?.scrollIntoView({ block: "end" });
  }, [count]);

  if (data.loading && !data.data) return <Spinner />;
  if (data.error || !data.data) return <Banner tone="danger">{data.error?.message ?? tr("Not found.")}</Banner>;
  const { conversation: c, messages, can_reply, can_create_ticket, tickets = [] } = data.data;
  const sel = messages.find((m) => m.id === selected) ?? [...messages].reverse().find((m) => m.role === "assistant") ?? null;

  async function reply(e: React.FormEvent) {
    e.preventDefault();
    if (!text.trim()) return;
    setBusy(true);
    setErr(null);
    try {
      await api.post(`/conversations/${id}/reply`, { text });
      setText("");
      await reload();
    } catch (e2) {
      setErr(errorText(e2));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="vr-ask">
      <section className="vr-ask-main">
        <div>
          <Link className="vr-link" href="/conversations" style={{ fontSize: 13 }}>
            {tr("← Conversations")}
          </Link>
        </div>
        <header style={{ display: "flex", alignItems: "flex-start", gap: 12, flexWrap: "wrap" }}>
          <div style={{ flex: 1, minWidth: 0 }}>
            <h1 className="vr-h" style={{ margin: 0 }}>
              {c.title || tr("New conversation")}
            </h1>
            <span className="vr-muted" style={{ fontSize: 13 }}>
              {[c.visitor_email || c.visitor_name || "Anonymous visitor", c.page_url, `started ${dateTime(c.created_at)}`].filter(Boolean).join(" · ")}
            </span>
          </div>
          {c.status === "escalated" ? (
            <Badge tone="warn" dot>
              {tr("Needs a person")}
            </Badge>
          ) : c.status === "resolved" ? (
            <Badge dot>{tr("Resolved")}</Badge>
          ) : (
            <Badge tone="brand" dot>
              {tr("With the assistant")}
            </Badge>
          )}
          {tickets.map((t) => (
            <Link key={t.id} href={`/tickets/${t.id}`} className="vr-link" style={{ fontSize: 13 }}>
              {tr("Ticket")} <span className="vr-mono">#{t.number}</span> · {tr(STATUS[t.status].label)}
            </Link>
          ))}
          {can_create_ticket && !converting ? <Button onClick={() => setConverting(true)}>{tr("Create ticket")}</Button> : null}
          {can_reply && c.status !== "resolved" ? (
            <Button
              onClick={async () => {
                try {
                  await api.post(`/conversations/${id}/resolve`);
                  await reload();
                } catch (e) {
                  setErr(errorText(e));
                }
              }}
            >
              {tr("Resolve")}
            </Button>
          ) : null}
        </header>
        {converting ? (
          <ConvertCard
            conversation={c}
            onDone={async (ticketId) => {
              setConverting(false);
              if (ticketId) await reload();
            }}
          />
        ) : null}
        {messages.map((m) =>
          m.kind === "event" ? (
            <EventLine key={m.id} m={m} />
          ) : m.kind === "form" ? (
            <div key={m.id} className="vr-event" role="note">
              <span>
                {tr("Form “")}{m.data.form?.title}”{" "}
                {m.data.state === "submitted"
                  ? tr("sent by the customer: ticket #{ticket_number}", { ticket_number: m.data.ticket_number })
                  : m.data.state === "dismissed"
                    ? tr("closed by the customer")
                    : m.data.reason === "handoff_timeout"
                      ? tr("offered because nobody replied in time")
                      : m.data.reason === "visitor_choice"
                        ? tr("opened by the customer")
                        : tr("offered by the assistant")}
              </span>
              <span className="vr-mono">{dateTime(m.created_at)}</span>
            </div>
          ) : m.role === "visitor" ? (
            <div key={m.id} className="vr-bubble">
              {m.data.channel === "email" ? <span className="vr-bubble-tag">{tr("By email")}</span> : null}
              {m.content}
            </div>
          ) : m.role === "agent" ? (
            <div key={m.id} className="vr-agent-msg">
              <span className="vr-eyebrow">
                {m.author ?? tr("Team")} · {dateTime(m.created_at)}
                {m.data.emailed_to ? tr(" · also emailed to {emailed_to}", { emailed_to: m.data.emailed_to }) : ""}
              </span>
              <p style={{ margin: 0, whiteSpace: "pre-line" }}>{m.content}</p>
            </div>
          ) : m.role === "assistant" ? (
            <div key={m.id} onClick={() => setSelected(m.id)}>
              <AssistantAnswer
                m={m}
                active={sel?.id === m.id ? active : null}
                onCite={(n) => {
                  setSelected(m.id);
                  setActive(n);
                }}
              />
            </div>
          ) : null,
        )}
        <div ref={endRef} />
        {err ? <Banner tone="danger">{err}</Banner> : null}
        {can_reply ? (
          <form className="vr-query" onSubmit={reply}>
            <textarea
              className="vr-query-input"
              rows={2}
              placeholder={c.status === "escalated" ? tr("Reply to the customer…") : tr("Reply to take over from the assistant…")}
              value={text}
              aria-label={tr("Reply")}
              onChange={(e) => setText(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) reply(e);
              }}
            />
            <div className="vr-query-bar">
              <span className="vr-query-hint">
                {c.status === "escalated" ? tr("The customer sees this in the chat within a few seconds") : tr("Replying hands the conversation to you; the assistant stops answering")}
                {c.visitor_email ? tr(", and by email at {visitor_email}.", { visitor_email: c.visitor_email }) : "."}
              </span>
              <div className="vr-query-meta">
                <span className="vr-query-hint">{tr("⌘⏎ to send")}</span>
                <button type="submit" className="vr-send" disabled={busy || !text.trim()} aria-label={tr("Send reply")}>
                  {busy ? <span className="vr-spin" aria-hidden="true" /> : <svg className="vr-icon" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M5 12h13M12 5l7 7-7 7" /></svg>}
                </button>
              </div>
            </div>
          </form>
        ) : (
          <p className="vr-muted" style={{ textAlign: "center", fontSize: 13 }}>
            {tr("Your role can read conversations but not reply.")}
          </p>
        )}
      </section>
      {sel ? <SourcesRail m={sel} active={active} onSelect={setActive} /> : null}
    </div>
  );
}

function ConvertCard({ conversation, onDone }: { conversation: Conversation; onDone: (ticketId: string | null) => void }) {
  const cats = useData<TicketCategory[]>("/ticket-categories");
  const [subject, setSubject] = useState(conversation.title || "");
  const [category, setCategory] = useState("");
  const [priority, setPriority] = useState("normal");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  return (
    <Card title={tr("Turn this conversation into a ticket")}>
      <form
        className="vr-form"
        onSubmit={async (e) => {
          e.preventDefault();
          setBusy(true);
          setErr(null);
          try {
            const t = await api.post<{ id: string }>(`/conversations/${conversation.id}/ticket`, { subject, category_id: category || null, priority });
            onDone(t.id);
          } catch (e2) {
            setErr(errorText(e2));
            setBusy(false);
          }
        }}
      >
        <p className="vr-muted" style={{ margin: 0 }}>
          {tr("The ticket keeps this whole conversation. The chat stays as it is; replies you write on the ticket appear in it.")}
        </p>
        <Field label={tr("Subject")} value={subject} onChange={(e) => setSubject(e.target.value)} maxLength={200} />
        <div className="vr-form-row">
          <Select label={tr("Category")} value={category} onChange={setCategory} options={[{ value: "", label: tr("No category") }, ...(cats.data ?? []).map((x) => ({ value: x.id, label: x.name }))]} />
          <Select label={tr("Priority")} value={priority} onChange={setPriority} options={PRIORITY_ORDER.map((p) => ({ value: p, label: tr(PRIORITY[p].label) }))} />
        </div>
        {err ? <Banner tone="danger">{err}</Banner> : null}
        <div className="vr-actions">
          <Button type="submit" variant="primary" busy={busy}>
            {tr("Create ticket")}
          </Button>
          <Button variant="ghost" onClick={() => onDone(null)}>
            {tr("Cancel")}
          </Button>
        </div>
      </form>
    </Card>
  );
}
