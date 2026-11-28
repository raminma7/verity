"use client";

import { t as tr, tp } from "@/i18n";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense, useEffect, useState } from "react";

import { ConfidenceBadge } from "@/components/rag";
import { Badge, Banner, EmptyState, Field, PageHeader, Segmented, Spinner } from "@/components/ui";
import type { Conversation } from "@/lib/api";
import { ago } from "@/lib/knowledge";
import { useData } from "@/lib/use-data";

export default function ConversationsPage() {
  return (
    <Suspense fallback={<Spinner />}>
      <Conversations />
    </Suspense>
  );
}

function StatusPill({ c }: { c: Conversation }) {
  if (c.status === "escalated") return <Badge tone="warn" dot>{tr("Needs a person")}</Badge>;
  if (c.status === "resolved") return <Badge dot>{tr("Resolved")}</Badge>;
  return <Badge tone="brand" dot>{tr("With the assistant")}</Badge>;
}

function dayLabel(iso: string | null) {
  if (!iso) return "Earlier";
  const d = new Date(iso);
  const today = new Date();
  const y = new Date(Date.now() - 86400000);
  if (d.toDateString() === today.toDateString()) return "Today";
  if (d.toDateString() === y.toDateString()) return "Yesterday";
  return d.toLocaleDateString("en-GB", { weekday: "long", day: "numeric", month: "short" });
}

function Conversations() {
  const params = useSearchParams();
  const bot = params.get("chatbot");
  const [status, setStatus] = useState(params.get("status") ?? "all");
  const [q, setQ] = useState("");
  const [term, setTerm] = useState("");
  useEffect(() => {
    const t = setTimeout(() => setTerm(q.trim()), 300);
    return () => clearTimeout(t);
  }, [q]);
  const query = new URLSearchParams();
  if (status !== "all") query.set("status", status);
  if (bot) query.set("chatbot_id", bot);
  if (term) query.set("q", term);
  const data = useData<{ items: Conversation[]; waiting: number }>(`/conversations?${query.toString()}`);
  const { reload } = data;
  useEffect(() => {
    const t = setInterval(reload, 10000);
    return () => clearInterval(t);
  }, [reload]);

  const groups: [string, Conversation[]][] = [];
  for (const c of data.data?.items ?? []) {
    const label = dayLabel(c.last_message_at);
    const g = groups.find((x) => x[0] === label);
    if (g) g[1].push(c);
    else groups.push([label, [c]]);
  }

  return (
    <>
      <PageHeader title={tr("Conversations")} subtitle={tr("Every chatbot conversation, with the evidence behind each answer. Conversations handed to the team wait here for a reply.")} />
      {data.data?.waiting ? (
        <Banner tone="warn" title={tp("{n} customer is waiting for a person", "{n} customers are waiting for a person", data.data.waiting)}>
          {tr("The assistant stays quiet in these conversations until someone resolves them.")}
        </Banner>
      ) : null}
      <div style={{ display: "flex", alignItems: "center", gap: 12, flexWrap: "wrap" }}>
        <div style={{ flex: "1 1 260px", maxWidth: 420 }}>
          <Field icon="search" placeholder={tr("Search questions or emails…")} value={q} onChange={(e) => setQ(e.target.value)} aria-label={tr("Search conversations")} />
        </div>
        <Segmented
          label={tr("Status")}
          value={status}
          onChange={setStatus}
          options={[
            { value: "all", label: tr("All") },
            { value: "escalated", label: data.data?.waiting ? tr("Needs a person · {n}", { n: data.data.waiting }) : tr("Needs a person") },
            { value: "open", label: tr("With the assistant") },
            { value: "resolved", label: tr("Resolved") },
          ]}
        />
      </div>
      {data.loading && !data.data ? (
        <Spinner />
      ) : data.error ? (
        <Banner tone="danger">{data.error.message}</Banner>
      ) : groups.length === 0 ? (
        <EmptyState icon="chat" title={tr("No conversations")}>
          {tr("Conversations appear here once customers use a chatbot on your site.")}
        </EmptyState>
      ) : (
        groups.map(([label, items]) => (
          <section key={label} style={{ display: "flex", flexDirection: "column", gap: 8 }}>
            <span className="vr-eyebrow">{label}</span>
            <div className="vr-card vr-list">
              {items.map((c) => (
                <Link key={c.id} href={`/conversations/${c.id}`} style={{ display: "flex", alignItems: "center", gap: 14 }}>
                  <span style={{ flex: 1, minWidth: 0, display: "flex", flexDirection: "column", gap: 2 }}>
                    <span style={{ fontWeight: 500, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{c.title || tr("New conversation")}</span>
                    <span className="vr-muted" style={{ fontSize: 12 }}>
                      {[c.visitor_email || c.visitor_name || "Anonymous visitor", c.origin?.replace(/^https?:\/\//, ""), `${c.message_count} messages`].filter(Boolean).join(" · ")}
                    </span>
                  </span>
                  {c.ticket_number ? <span className="vr-num">{tr("Ticket #")}{c.ticket_number}</span> : null}
                  {c.confidence ? <ConfidenceBadge confidence={c.confidence as "high"} /> : null}
                  <StatusPill c={c} />
                  <span className="vr-mono vr-muted" style={{ fontSize: 12, width: 80, textAlign: "right" }}>
                    {ago(c.last_message_at)}
                  </span>
                </Link>
              ))}
            </div>
          </section>
        ))
      )}
    </>
  );
}
