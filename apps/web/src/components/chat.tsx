"use client";

import { t } from "@/i18n";
import { useState } from "react";

import type { ChatMessage, Citation as Cite } from "@/lib/api";

import { AnswerCard, CitedText, PipelineTrace, SourceCard } from "./rag";
import { Badge, dateTime, Segmented } from "./ui";

export const EVENT_TEXT: Record<string, (m: ChatMessage) => string> = {
  escalated: (m) =>
    m.data.reason === "no_answer"
      ? t("Handed to the team (no answer found). The assistant stays quiet until it’s resolved.")
      : m.data.reason === "ai_unavailable"
        ? t("Handed to the team (AI unavailable). The assistant stays quiet until it’s resolved.")
        : t("Handed to the team. The assistant stays quiet until it’s resolved."),
  agent_joined: (m) => (m.data.name ? t("{name} joined the conversation.", { name: m.data.name }) : t("A team member joined the conversation.")),
  resolved: (m) =>
    m.data.name ? t("{name} resolved the conversation. The assistant answers new messages again.", { name: m.data.name }) : t("The team resolved the conversation. The assistant answers new messages again."),
  ticket_created: (m) =>
    m.data.reason === "agent_conversion"
      ? t("Turned into ticket #{n}.", { n: m.data.ticket_number })
      : m.data.reason === "handoff_timeout"
        ? t("Nobody replied in time: ticket #{n} opened with the visitor’s email. The assistant has the chat again.", { n: m.data.ticket_number })
        : t("The customer sent a form: ticket #{n}. The assistant has the chat again.", { n: m.data.ticket_number }),
};

/** An assistant answer: AnswerCard with cited text; clicking a citation selects its source. */
export function AssistantAnswer({ m, active, onCite, onCopy }: { m: ChatMessage; active?: number | null; onCite?: (n: number) => void; onCopy?: () => void }) {
  const titles = Object.fromEntries(m.citations.map((c) => [c.n, c.title]));
  const outcome = m.data.outcome;
  return (
    <AnswerCard
      confidence={outcome === "answered" ? m.confidence : outcome === "fallback" ? undefined : "none"}
      label={outcome === "fallback" ? t("Fallback message") : t("Answer")}
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
        label={t("Answer details")}
        value={tab}
        onChange={setTab}
        options={[
          { value: "sources", label: t("Sources · {length}", { length: cites.length }) },
          { value: "trace", label: t("How it was answered") },
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
              {t("No sources found")}
            </Badge>
            <p className="vr-muted" style={{ margin: "8px 0 0" }}>
              {m.data.outcome === "fallback" ? t("The assistant couldn’t answer, so the fallback message was shown.") : t("No passage was relevant enough, so the model wasn’t called.")}
            </p>
            {m.data.sources?.length ? (
              <p className="vr-help" style={{ marginTop: 8 }}>
                {t("Closest:")} {m.data.sources.slice(0, 3).map((s) => `${s.title} (${s.score.toFixed(2)})`).join(", ")}
              </p>
            ) : null}
          </div>
        )
      ) : (
        <div className="vr-card" style={{ padding: 20 }}>
          <div className="vr-eyebrow" style={{ marginBottom: 14 }}>
            {t("How it was answered")}
          </div>
          <PipelineTrace steps={m.trace ?? []} />
        </div>
      )}
    </aside>
  );
}
