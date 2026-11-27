"use client";

import Link from "next/link";

import { Banner, Card, compact, date, EmptyState, fmt, PageHeader, Spinner, Stat, UsageMeter } from "@/components/ui";
import type { UsageSummary } from "@/lib/api";
import { useSession } from "@/lib/session";
import { useData } from "@/lib/use-data";

type Row = { key: string; input_tokens: number; output_tokens: number; weighted: number; requests: number };
type Mult = { model: string; operation: string; input_multiplier: number; output_multiplier: number };

const OPS: Record<string, string> = {
  chat_response: "Chatbot answers",
  kb_ask: "Ask (dashboard)",
  tool_call: "API tool decisions",
  form_activation: "Form suggestions",
  form_prefill: "Form prefill",
  classification: "Email classification",
  summary: "Summaries",
  auto_reply: "Automatic replies",
  embedding: "Embeddings",
};

function Breakdown({ title, rows, label }: { title: string; rows: Row[]; label: (k: string) => string }) {
  return (
    <Card title={title} padded={false}>
      {rows.length === 0 ? (
        <EmptyState icon="chart" title="No AI usage this period" />
      ) : (
        <div className="vr-table-wrap">
          <table className="vr-table">
            <thead>
              <tr>
                <th>{title.replace("By ", "")}</th>
                <th className="num">Requests</th>
                <th className="num">Input</th>
                <th className="num">Output</th>
                <th className="num">Weighted</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.key}>
                  <td>{label(r.key)}</td>
                  <td className="num">{fmt(r.requests)}</td>
                  <td className="num">{fmt(r.input_tokens)}</td>
                  <td className="num">{fmt(r.output_tokens)}</td>
                  <td className="num">{fmt(r.weighted)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Card>
  );
}

function Daily({ rows }: { rows: { date: string; actual: number; weighted: number }[] }) {
  const days: { date: string; weighted: number; actual: number }[] = [];
  const byDate = new Map(rows.map((r) => [r.date, r]));
  for (let i = 29; i >= 0; i--) {
    const d = new Date(Date.now() - i * 86400000).toISOString().slice(0, 10);
    days.push(byDate.get(d) ?? { date: d, weighted: 0, actual: 0 });
  }
  const max = Math.max(1, ...days.map((d) => d.weighted));
  return (
    <Card title="Daily weighted tokens · last 30 days">
      <div className="vr-bars" role="img" aria-label="Daily weighted token usage for the last 30 days">
        {days.map((d) => (
          <div key={d.date} title={`${d.date}: ${fmt(d.weighted)} weighted (${fmt(d.actual)} actual)`}>
            <span className={d.weighted ? undefined : "is-zero"} style={{ height: `${(d.weighted / max) * 100}%` }} />
          </div>
        ))}
      </div>
      <div className="vr-bars-axis">
        <span>{date(days[0].date)}</span>
        <span>Today</span>
      </div>
    </Card>
  );
}

export default function Usage() {
  const { current } = useSession();
  const summary = useData<UsageSummary>("/usage/summary");
  const daily = useData<{ date: string; actual: number; weighted: number }[]>("/usage/daily?days=30");
  const breakdown = useData<{ by_operation: Row[]; by_model: Row[] }>("/usage/breakdown");
  const mult = useData<Mult[]>("/usage/multipliers");

  if (summary.loading && !summary.data) return <Spinner />;
  if (summary.error) return <Banner tone="danger">{summary.error.message}</Banner>;
  const s = summary.data!;

  if (!s.active)
    return (
      <>
        <PageHeader eyebrow="AI usage" title="Token usage" />
        <Card>
          <EmptyState icon="chart" title="No active token period" action={<Link className="vr-btn vr-btn-primary vr-btn-md" href="/plans">See plans</Link>}>
            {current?.subscription ? "The subscription isn’t active, so there’s no allowance to track." : "Choose a plan to get a monthly AI token allowance."}
          </EmptyState>
        </Card>
      </>
    );

  return (
    <>
      <PageHeader
        eyebrow="AI usage"
        title="Token usage"
        subtitle={`Period ${date(s.period_start)} – ${date(s.period_end)}. Shared by every member and chatbot in ${current?.workspace.name}.`}
      />
      {!s.ai_available ? (
        <Banner tone="danger" title="Allowance used up. AI features are paused." action={<Link className="vr-btn vr-btn-secondary vr-btn-sm" href="/plans">Upgrade</Link>}>
          The chatbot shows its fallback message. Manual ticketing, email ingestion and forms keep working. The allowance resets on {date(s.period_end)}.
        </Banner>
      ) : s.percent_used >= 80 ? (
        <Banner tone="warn" title={`${Math.floor(s.percent_used)}% used.`}>We email billing contacts at 80% and 90%.</Banner>
      ) : null}

      <div className="vr-grid vr-grid-4">
        <Stat label="Remaining" value={compact(s.remaining)} note="weighted tokens" />
        <Stat label="Used" value={`${s.percent_used}%`} note={`${fmt(s.consumed_weighted)} of ${fmt(s.total_allowance)}`} />
        <Stat label="Actual tokens" value={compact(s.actual_input_tokens + s.actual_output_tokens)} note={`${compact(s.actual_input_tokens)} in · ${compact(s.actual_output_tokens)} out`} />
        <Stat label="Renews" value={date(s.period_end).split(" ").slice(0, 2).join(" ")} note="Unused tokens don’t roll over" />
      </div>

      <Card>
        <div className="vr-form">
          <UsageMeter label="Weighted tokens this period" used={s.consumed_weighted} total={s.total_allowance} />
          <dl className="vr-kv" style={{ fontSize: 13 }}>
            <dt>Plan allowance</dt>
            <dd className="vr-mono">{fmt(s.allowance)}</dd>
            {s.bonus ? (
              <>
                <dt>Added tokens</dt>
                <dd className="vr-mono">{fmt(s.bonus)}</dd>
              </>
            ) : null}
            <dt>In progress</dt>
            <dd className="vr-mono">{fmt(s.reserved)} held for requests still running</dd>
            <dt>Embeddings</dt>
            <dd className="vr-mono">{fmt(s.embedding_weighted)} weighted, tracked separately below</dd>
          </dl>
        </div>
      </Card>

      {daily.data ? <Daily rows={daily.data} /> : null}

      <div className="vr-grid vr-grid-2">
        <Breakdown title="By feature" rows={breakdown.data?.by_operation ?? []} label={(k) => OPS[k] ?? k} />
        <Breakdown title="By model" rows={breakdown.data?.by_model ?? []} label={(k) => k} />
      </div>

      <Card title="How weighted tokens are calculated" padded={false}>
        <div className="vr-panel-body" style={{ paddingBottom: 0 }}>
          <p className="vr-muted" style={{ fontSize: 13 }}>
            Models cost different amounts to run, so each request counts as <span className="vr-code">input × input weight + output × output weight</span>. A weight for a specific feature overrides the model’s default (<span className="vr-code">*</span>).
          </p>
        </div>
        <div className="vr-table-wrap" style={{ marginTop: 16 }}>
          <table className="vr-table">
            <thead>
              <tr>
                <th>Model</th>
                <th>Feature</th>
                <th className="num">Input weight</th>
                <th className="num">Output weight</th>
              </tr>
            </thead>
            <tbody>
              {(mult.data ?? []).map((m) => (
                <tr key={`${m.model}:${m.operation}`}>
                  <td className="vr-mono" style={{ fontSize: 13 }}>
                    {m.model}
                  </td>
                  <td>{m.operation === "*" ? "All features" : (OPS[m.operation] ?? m.operation)}</td>
                  <td className="num">×{m.input_multiplier}</td>
                  <td className="num">×{m.output_multiplier}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>
    </>
  );
}
