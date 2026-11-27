"use client";

import { t } from "@/i18n";
import Link from "next/link";

import { Banner, Card, compact, date, EmptyState, fmt, PageHeader, Spinner, Stat, UsageMeter } from "@/components/ui";
import type { UsageSummary } from "@/lib/api";
import { useSession } from "@/lib/session";
import { OPS } from "@/lib/usage";
import { useData } from "@/lib/use-data";

type Row = { key: string; input_tokens: number; output_tokens: number; weighted: number; requests: number };
type Mult = { model: string; operation: string; input_multiplier: number; output_multiplier: number };


function Breakdown({ title, rows, label }: { title: string; rows: Row[]; label: (k: string) => string }) {
  return (
    <Card title={title} padded={false}>
      {rows.length === 0 ? (
        <EmptyState icon="chart" title={t("No AI usage this period")} />
      ) : (
        <div className="vr-table-wrap">
          <table className="vr-table">
            <thead>
              <tr>
                <th>{title.replace("By ", "")}</th>
                <th className="num">{t("Requests")}</th>
                <th className="num">{t("Input")}</th>
                <th className="num">{t("Output")}</th>
                <th className="num">{t("Weighted")}</th>
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
    <Card title={t("Daily weighted tokens · last 30 days")}>
      <div className="vr-bars" role="img" aria-label={t("Daily weighted token usage for the last 30 days")}>
        {days.map((d) => (
          <div key={d.date} title={t("{date}: {weighted} weighted ({actual} actual)", { date: d.date, weighted: fmt(d.weighted), actual: fmt(d.actual) })}>
            <span className={d.weighted ? undefined : "is-zero"} style={{ height: `${(d.weighted / max) * 100}%` }} />
          </div>
        ))}
      </div>
      <div className="vr-bars-axis">
        <span>{date(days[0].date)}</span>
        <span>{t("Today")}</span>
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
        <PageHeader eyebrow={t("AI usage")} title={t("Token usage")} />
        <Card>
          <EmptyState icon="chart" title={t("No active token period")} action={<Link className="vr-btn vr-btn-primary vr-btn-md" href="/plans">{t("See plans")}</Link>}>
            {current?.subscription ? t("The subscription isn’t active, so there’s no allowance to track.") : t("Choose a plan to get a monthly AI token allowance.")}
          </EmptyState>
        </Card>
      </>
    );

  return (
    <>
      <PageHeader
        eyebrow={t("AI usage")}
        title={t("Token usage")}
        subtitle={t("Period {period_start} – {period_end}. Shared by every member and chatbot in {name}.", { period_start: date(s.period_start), period_end: date(s.period_end), name: current?.workspace.name })}
      />
      {!s.ai_available ? (
        <Banner tone="danger" title={t("Allowance used up. AI features are paused.")} action={<div className="vr-actions"><Link className="vr-btn vr-btn-secondary vr-btn-sm" href="/billing/tokens">{t("Buy tokens")}</Link><Link className="vr-btn vr-btn-secondary vr-btn-sm" href="/plans">{t("Upgrade")}</Link></div>}>
          {t("The chatbot shows its fallback message. Manual ticketing, email ingestion and forms keep working. The allowance resets on")} {date(s.period_end)}.
        </Banner>
      ) : s.percent_used >= 80 ? (
        <Banner tone="warn" title={t("{percent_used}% used.", { percent_used: Math.floor(s.percent_used) })}>{t("We email billing contacts at 80% and 90%.")}</Banner>
      ) : null}

      <div className="vr-grid vr-grid-4">
        <Stat label={t("Remaining")} value={compact(s.remaining)} note={t("weighted tokens")} />
        <Stat label={t("Used")} value={`${s.percent_used}%`} note={t("{consumed_weighted} of {total_allowance}", { consumed_weighted: fmt(s.consumed_weighted), total_allowance: fmt(s.total_allowance) })} />
        <Stat label={t("Actual tokens")} value={compact(s.actual_input_tokens + s.actual_output_tokens)} note={t("{actual_input_tokens} in · {actual_output_tokens} out", { actual_input_tokens: compact(s.actual_input_tokens), actual_output_tokens: compact(s.actual_output_tokens) })} />
        <Stat label={t("Renews")} value={date(s.period_end).split(" ").slice(0, 2).join(" ")} note={t("Unused plan tokens don’t roll over")} />
      </div>

      <Card>
        <div className="vr-form">
          <UsageMeter label={t("Weighted tokens this period")} used={s.consumed_weighted} total={s.total_allowance} />
          <dl className="vr-kv" style={{ fontSize: 13 }}>
            <dt>{t("Plan allowance")}</dt>
            <dd className="vr-mono">{fmt(s.allowance)}</dd>
            {s.bonus ? (
              <>
                <dt>{t("Added tokens")}</dt>
                <dd className="vr-mono">{fmt(s.bonus)}</dd>
              </>
            ) : null}
            <dt>{t("Token packages")}</dt>
            <dd className="vr-mono">
              {s.package_remaining ? t("{package_remaining} left, used after the allowance · next expiry {package_next_expiry}", { package_remaining: fmt(s.package_remaining), package_next_expiry: date(s.package_next_expiry) }) : t("None")}{" "}
              <Link href="/billing/tokens">{t("Buy tokens")}</Link>
            </dd>
            <dt>{t("In progress")}</dt>
            <dd className="vr-mono">{fmt(s.reserved)} {t("held for requests still running")}</dd>
            <dt>{t("Embeddings")}</dt>
            <dd className="vr-mono">{fmt(s.embedding_weighted)} {t("weighted, tracked separately below")}</dd>
          </dl>
        </div>
      </Card>

      {daily.data ? <Daily rows={daily.data} /> : null}

      <div className="vr-grid vr-grid-2">
        <Breakdown title={t("By feature")} rows={breakdown.data?.by_operation ?? []} label={(k) => OPS[k] ?? k} />
        <Breakdown title={t("By model")} rows={breakdown.data?.by_model ?? []} label={(k) => k} />
      </div>

      <Card title={t("How weighted tokens are calculated")} padded={false}>
        <div className="vr-panel-body" style={{ paddingBottom: 0 }}>
          <p className="vr-muted" style={{ fontSize: 13 }}>
            {t("Models cost different amounts to run, so each request counts as")} <span className="vr-code">{t("input × input weight + output × output weight")}</span>{t(". A weight for a specific feature overrides the model’s default (")}<span className="vr-code">*</span>).
          </p>
        </div>
        <div className="vr-table-wrap" style={{ marginTop: 16 }}>
          <table className="vr-table">
            <thead>
              <tr>
                <th>{t("Model")}</th>
                <th>{t("Feature")}</th>
                <th className="num">{t("Input weight")}</th>
                <th className="num">{t("Output weight")}</th>
              </tr>
            </thead>
            <tbody>
              {(mult.data ?? []).map((m) => (
                <tr key={`${m.model}:${m.operation}`}>
                  <td className="vr-mono" style={{ fontSize: 13 }}>
                    {m.model}
                  </td>
                  <td>{m.operation === "*" ? t("All features") : (t(OPS[m.operation]) ?? m.operation)}</td>
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
