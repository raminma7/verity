"use client";

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
  if (data.error || !data.data) return <Banner tone="danger">{data.error?.message ?? "Not found."}</Banner>;
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
            ← Conversations
          </Link>
        </div>
        <header style={{ display: "flex", alignItems: "flex-start", gap: 12, flexWrap: "wrap" }}>
          <div style={{ flex: 1, minWidth: 0 }}>
            <h1 className="vr-h" style={{ margin: 0 }}>
              {c.title || "New conversation"}
            </h1>
            <span className="vr-muted" style={{ fontSize: 13 }}>
              {[c.visitor_email || c.visitor_name || "Anonymous visitor", c.page_url, `started ${dateTime(c.created_at)}`].filter(Boolean).join(" · ")}
            </span>
          </div>
          {c.status === "escalated" ? (
            <Badge tone="warn" dot>
              Needs a person
            </Badge>
          ) : c.status === "resolved" ? (
            <Badge dot>Resolved</Badge>
          ) : (
            <Badge tone="brand" dot>
              With the assistant
            </Badge>
          )}
          {tickets.map((t) => (
            <Link key={t.id} href={`/tickets/${t.id}`} className="vr-link" style={{ fontSize: 13 }}>
              Ticket <span className="vr-mono">#{t.number}</span> · {STATUS[t.status].label}
            </Link>
          ))}
          {can_create_ticket && !converting ? <Button onClick={() => setConverting(true)}>Create ticket</Button> : null}
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
              Resolve
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
                Form “{m.data.form?.title}”{" "}
                {m.data.state === "submitted"
                  ? `sent by the customer: ticket #${m.data.ticket_number}`
                  : m.data.state === "dismissed"
                    ? "closed by the customer"
                    : m.data.reason === "handoff_timeout"
                      ? "offered because nobody replied in time"
                      : m.data.reason === "visitor_choice"
                        ? "opened by the customer"
                        : "offered by the assistant"}
              </span>
              <span className="vr-mono">{dateTime(m.created_at)}</span>
            </div>
          ) : m.role === "visitor" ? (
            <div key={m.id} className="vr-bubble">
              {m.content}
            </div>
          ) : m.role === "agent" ? (
            <div key={m.id} className="vr-agent-msg">
              <span className="vr-eyebrow">{m.author ?? "Team"} · {dateTime(m.created_at)}</span>
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
              placeholder={c.status === "escalated" ? "Reply to the customer…" : "Reply to take over from the assistant…"}
              value={text}
              aria-label="Reply"
              onChange={(e) => setText(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) reply(e);
              }}
            />
            <div className="vr-query-bar">
              <span className="vr-query-hint">{c.status === "escalated" ? "The customer sees this in the chat within a few seconds." : "Replying hands the conversation to you; the assistant stops answering."}</span>
              <div className="vr-query-meta">
                <span className="vr-query-hint">⌘⏎ to send</span>
                <button type="submit" className="vr-send" disabled={busy || !text.trim()} aria-label="Send reply">
                  {busy ? <span className="vr-spin" aria-hidden="true" /> : <svg className="vr-icon" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M5 12h13M12 5l7 7-7 7" /></svg>}
                </button>
              </div>
            </div>
          </form>
        ) : (
          <p className="vr-muted" style={{ textAlign: "center", fontSize: 13 }}>
            Your role can read conversations but not reply.
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
    <Card title="Turn this conversation into a ticket">
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
          The ticket keeps this whole conversation. The chat stays as it is; replies you write on the ticket appear in it.
        </p>
        <Field label="Subject" value={subject} onChange={(e) => setSubject(e.target.value)} maxLength={200} />
        <div className="vr-form-row">
          <Select label="Category" value={category} onChange={setCategory} options={[{ value: "", label: "No category" }, ...(cats.data ?? []).map((x) => ({ value: x.id, label: x.name }))]} />
          <Select label="Priority" value={priority} onChange={setPriority} options={PRIORITY_ORDER.map((p) => ({ value: p, label: PRIORITY[p].label }))} />
        </div>
        {err ? <Banner tone="danger">{err}</Banner> : null}
        <div className="vr-actions">
          <Button type="submit" variant="primary" busy={busy}>
            Create ticket
          </Button>
          <Button variant="ghost" onClick={() => onDone(null)}>
            Cancel
          </Button>
        </div>
      </form>
    </Card>
  );
}
