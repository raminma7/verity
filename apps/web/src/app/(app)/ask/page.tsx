"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useRef, useState } from "react";

import { AssistantAnswer, SourcesRail } from "@/components/chat";
import { AnswerCard, QueryBar } from "@/components/rag";
import { Banner, Button, EmptyState, PageHeader, Spinner } from "@/components/ui";
import { api, errorText, streamEvents, type ChatMessage, type Conversation } from "@/lib/api";
import { useSession } from "@/lib/session";
import { useData } from "@/lib/use-data";

export default function AskPage() {
  return (
    <Suspense fallback={<Spinner />}>
      <Ask />
    </Suspense>
  );
}

function Ask() {
  const { user, current } = useSession();
  const params = useSearchParams();
  const router = useRouter();
  const convId = params.get("c");
  const history = useData<Conversation[]>("/ask/conversations");
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [streaming, setStreaming] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [active, setActive] = useState<number | null>(null);
  const [loading, setLoading] = useState(false);
  const endRef = useRef<HTMLDivElement>(null);
  const idRef = useRef<string | null>(convId);
  const createdHere = useRef<string | null>(null);

  useEffect(() => {
    idRef.current = convId;
    if (!convId) {
      setMessages([]);
      return;
    }
    if (createdHere.current === convId) return; // we just started it; the stream is filling it in
    setLoading(true);
    api
      .get<{ messages: ChatMessage[] }>(`/conversations/${convId}`)
      .then((d) => {
        setMessages(d.messages);
        const last = [...d.messages].reverse().find((m) => m.role === "assistant");
        setSelected(last?.id ?? null);
      })
      .catch((e) => setErr(errorText(e)))
      .finally(() => setLoading(false));
  }, [convId]);

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [messages.length, streaming]);

  async function ask(text: string) {
    setBusy(true);
    setErr(null);
    setStreaming("");
    setActive(null);
    try {
      await streamEvents("/api/ask", { text, conversation_id: idRef.current }, (event, data) => {
        if (event === "conversation") {
          const c = data as Conversation;
          if (!idRef.current) {
            idRef.current = c.id;
            createdHere.current = c.id;
            router.replace(`/ask?c=${c.id}`, { scroll: false });
          }
        } else if (event === "delta") {
          setStreaming((s) => (s ?? "") + (data as { text: string }).text);
        } else if (event === "message") {
          const m = data as ChatMessage;
          setMessages((prev) => [...prev, m]);
          if (m.role === "assistant") {
            setStreaming(null);
            setSelected(m.id);
          }
        }
      });
      history.reload();
    } catch (e) {
      setErr(errorText(e));
    } finally {
      setBusy(false);
      setStreaming(null);
    }
  }

  const sel = messages.find((m) => m.id === selected) ?? null;
  const first = user.full_name.split(" ")[0];
  const noKnowledge = current && !current.subscription?.active;

  return (
    <div className="vr-ask">
      <section className="vr-ask-main">
        {messages.length === 0 && !loading && streaming === null ? (
          <>
            <PageHeader title={`What do you want to know, ${first}?`} subtitle="Ask the knowledge base exactly as a customer would. You’ll see the answer the chatbot would give, with its sources." />
            {noKnowledge ? <Banner tone="warn">Ask needs an active plan.</Banner> : null}
            <QueryBar busy={busy} onSubmit={ask} rows={3} autoFocus />
            {history.data?.length ? (
              <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
                <span className="vr-eyebrow">Recent questions</span>
                <div className="vr-card vr-list">
                  {history.data.slice(0, 8).map((c) => (
                    <Link key={c.id} href={`/ask?c=${c.id}`} style={{ display: "flex", justifyContent: "space-between", gap: 12 }}>
                      <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{c.title || "Untitled"}</span>
                      <span className="vr-muted vr-mono" style={{ fontSize: 12, flex: "none" }}>
                        {c.message_count / 2} {c.message_count === 2 ? "question" : "questions"}
                      </span>
                    </Link>
                  ))}
                </div>
              </div>
            ) : (
              <EmptyState icon="chat" title="No questions yet">
                Answers come only from your knowledge base, so add documents or a website first.
              </EmptyState>
            )}
          </>
        ) : (
          <>
            <header style={{ display: "flex", alignItems: "center", gap: 12 }}>
              <h1 className="vr-h" style={{ flex: 1, margin: 0 }}>
                {messages.find((m) => m.role === "member")?.content.slice(0, 120) ?? "Ask"}
              </h1>
              <Button icon="plus" onClick={() => router.push("/ask")}>
                New question
              </Button>
            </header>
            {loading ? <Spinner /> : null}
            {messages.map((m) =>
              m.role === "member" ? (
                <div key={m.id} className="vr-bubble">
                  {m.content}
                </div>
              ) : m.role === "assistant" ? (
                <div key={m.id} onClick={() => setSelected(m.id)} className={m.id === selected ? "vr-answer-selected" : undefined}>
                  <AssistantAnswer
                    m={m}
                    active={m.id === selected ? active : null}
                    onCite={(n) => {
                      setSelected(m.id);
                      setActive(n);
                    }}
                    onCopy={() => navigator.clipboard?.writeText(m.content)}
                  />
                </div>
              ) : null,
            )}
            {streaming !== null ? (
              <AnswerCard confidence={undefined}>{streaming ? <p style={{ whiteSpace: "pre-line" }}>{streaming}</p> : <p className="vr-muted">Searching the knowledge base…</p>}</AnswerCard>
            ) : null}
            {err ? <Banner tone="danger">{err}</Banner> : null}
            <div ref={endRef} />
            <QueryBar busy={busy} onSubmit={ask} rows={1} placeholder="Ask a follow-up…" />
            <p className="vr-muted" style={{ margin: 0, textAlign: "center", fontSize: 12, lineHeight: "16px" }}>
              Answers come only from your documents. Check the cited passages before acting on them.
            </p>
          </>
        )}
      </section>
      {sel ? <SourcesRail m={sel} active={active} onSelect={setActive} /> : null}
    </div>
  );
}
