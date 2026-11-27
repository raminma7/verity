"use client";

import { useState } from "react";

import type { ChatMessage, Citation as Cite } from "@/lib/api";

import { AnswerCard, CitedText, PipelineTrace, SourceCard } from "./rag";
import { Badge, dateTime, Segmented } from "./ui";

export const EVENT_TEXT: Record<string, (m: ChatMessage) => string> = {
  escalated: (m) => `Handed to the team${m.data.reason === "no_answer" ? " (no answer found)" : m.data.reason === "ai_unavailable" ? " (AI unavailable)" : ""}. The assistant stays quiet until it’s resolved.`,
  agent_joined: (m) => `${m.data.name ?? "A team member"} joined the conversation.`,
  resolved: (m) => `${m.data.name ?? "The team"} resolved the conversation. The assistant answers new messages again.`,
  ticket_created: (m) =>
    m.data.reason === "agent_conversion" ? `Turned into ticket #${m.data.ticket_number}.` : `The customer sent a form: ticket #${m.data.ticket_number}. The assistant has the chat again.`,
};

/** An assistant answer: AnswerCard with cited text; clicking a citation selects its source. */
export function AssistantAnswer({ m, active, onCite, onCopy }: { m: ChatMessage; active?: number | null; onCite?: (n: number) => void; onCopy?: () => void }) {
  const titles = Object.fromEntries(m.citations.map((c) => [c.n, c.title]));
  const outcome = m.data.outcome;
  return (
    <AnswerCard
      confidence={outcome === "answered" ? m.confidence : outcome === "fallback" ? undefined : "none"}
      label={outcome === "fallback" ? "Fallback message" : "Answer"}
      sourceCount={outcome === "answered" ? m.citations.length : undefined}
      model={m.model}
      latencyMs={m.latency_ms}
      onCopy={onCopy}
    >
      {m.data.blocks?.length ? <CitedText blocks={m.data.blocks} active={active} onCite={onCite} titles={titles} /> : <p>{m.content}</p>}
    </AnswerCard>
  );
}

export function EventLine({ m }: { m: ChatMessage }) {
  const f = EVENT_TEXT[m.data.event ?? ""];
  return (
    <div className="vr-event" role="note">
      <span>{f ? f(m) : m.data.event}</span>
      <span className="vr-mono">{dateTime(m.created_at)}</span>
    </div>
  );
}

/** Right rail: the selected answer's sources and how it was produced. */
export function SourcesRail({ m, active, onSelect }: { m: ChatMessage | null; active: number | null; onSelect: (n: number) => void }) {
  const [tab, setTab] = useState("sources");
  if (!m) return null;
  const cites: Cite[] = m.citations;
  return (
    <aside className="vr-rail">
      <Segmented
        block
        label="Answer details"
        value={tab}
        onChange={setTab}
        options={[
          { value: "sources", label: `Sources · ${cites.length}` },
          { value: "trace", label: "How it was answered" },
        ]}
      />
      {tab === "sources" ? (
        cites.length ? (
          cites.map((c) => (
            <SourceCard
              key={c.n}
              n={c.n}
              type={c.type}
              title={c.title}
              section={c.section || (c.page ? `Page ${c.page}` : null)}
              score={c.score}
              excerpt={c.excerpt || c.cited_text}
              highlight={c.cited_text}
              active={active === c.n}
              href={c.url}
              onClick={() => onSelect(c.n)}
            />
          ))
        ) : (
          <div className="vr-card" style={{ padding: 16 }}>
            <Badge tone="danger" dot>
              No sources found
            </Badge>
            <p className="vr-muted" style={{ margin: "8px 0 0" }}>
              {m.data.outcome === "fallback" ? "The assistant couldn’t answer, so the fallback message was shown." : "No passage was relevant enough, so the model wasn’t called."}
            </p>
            {m.data.sources?.length ? (
              <p className="vr-help" style={{ marginTop: 8 }}>
                Closest: {m.data.sources.slice(0, 3).map((s) => `${s.title} (${s.score.toFixed(2)})`).join(", ")}
              </p>
            ) : null}
          </div>
        )
      ) : (
        <div className="vr-card" style={{ padding: 20 }}>
          <div className="vr-eyebrow" style={{ marginBottom: 14 }}>
            How it was answered
          </div>
          <PipelineTrace steps={m.trace ?? []} />
        </div>
      )}
    </aside>
  );
}
