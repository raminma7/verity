"use client";

import Link from "next/link";
import { useMemo, useState } from "react";

import { BarChart, BarList, ChartCard, delta, KPI, LineChart, minutes, pct, type Point, type Series, seriesTable } from "@/components/charts";
import { RangePicker, rangeQuery, type RangeOut } from "@/components/range";
import { CALL_WORD } from "@/components/tools";
import { Banner, Card, compact, EmptyState, fmt, PageHeader, Spinner } from "@/components/ui";
import type { ToolCallStatus } from "@/lib/api";
import { useSession } from "@/lib/session";
import { SOURCE } from "@/lib/tickets";
import { OPS } from "@/lib/usage";
import { useData } from "@/lib/use-data";

type TicketTotals = {
  created: number;
  responded: number;
  first_response_median_minutes: number | null;
  first_response_p90_minutes: number | null;
  resolved: number;
  resolution_median_minutes: number | null;
  resolution_p90_minutes: number | null;
  reopened: number;
  resolutions: number;
  reopen_rate: number | null;
};
type Tickets = {
  range: RangeOut;
  totals: TicketTotals;
  previous: TicketTotals;
  by_source: Point[];
  source_totals: Record<string, number>;
  by_category: Point[];
  category_totals: { name: string; count: number }[];
  response_times: Point[];
  reopens: Point[];
};
type Sla = {
  range: RangeOut;
  by_priority: {
    priority: string;
    targets: { first_response_minutes: number; resolution_minutes: number };
    tickets: number;
    response_met: number;
    response_breached: number;
    response_pending: number;
    resolution_met: number;
    resolution_breached: number;
    resolution_pending: number;
    response_met_rate: number | null;
    resolution_met_rate: number | null;
  }[];
  breaches: number;
  series: Point[];
  breaching_now: { id: string; number: number; subject: string; priority: string; target: string; overdue_minutes: number; assignee: string | null }[];
};
type Agents = {
  unassigned_open: number;
  members: {
    id: string;
    name: string;
    role: string;
    availability: string;
    max_open: number | null;
    open_now: number;
    assigned: number;
    replies: number;
    chat_replies: number;
    resolved: number;
    first_response_median_minutes: number | null;
  }[];
};
type ChatTotals = { conversations: number; deflected: number; handed_off: number; unanswered: number; deflection_rate: number | null; handoff_rate: number | null };
type Chatbot = { range: RangeOut; totals: ChatTotals; previous: ChatTotals; series: Point[]; chatbots: (ChatTotals & { id: string; name: string })[] };
type Tools = {
  totals: { calls: number; ok: number; from_chats: number; tests: number; success_rate: number | null; latency_p50_ms: number | null; latency_p95_ms: number | null };
  series: Point[];
  by_status: { status: ToolCallStatus; count: number }[];
  tools: { name: string; calls: number; ok: number; tests: number; success_rate: number | null; latency_p50_ms: number | null; latency_p95_ms: number | null }[];
};
type Email = {
  totals: { received: number; legitimate: number; spam: number; promotion: number; held: number; automatic: number; failed: number; marked_not_spam: number; decided_by_ai: number; spam_rate: number | null };
  series: Point[];
};
type Usage = { name: string; input_tokens: number; output_tokens: number; weighted_tokens: number; calls: number };
type Ai = { totals: { weighted_tokens: number; calls: number; input_tokens: number; output_tokens: number }; previous: { weighted_tokens: number }; series: Point[]; by_feature: Usage[]; by_model: Usage[] };

const PRIORITY: Record<string, string> = { urgent: "Urgent", high: "High", normal: "Normal", low: "Low" };

const sumKeys = (p: Point, keys: string[]) => keys.reduce((a, k) => a + (Number(p[k]) || 0), 0);

export default function Analytics() {
  const { can } = useSession();
  const [range, setRange] = useState({ preset: "30d", from: "", to: "" });
  const q = rangeQuery(range);
  const allowed = can("analytics.read");
  const tickets = useData<Tickets>(allowed ? `/analytics/tickets?${q}` : null);
  const sla = useData<Sla>(allowed ? `/analytics/sla?${q}` : null);
  const agents = useData<Agents>(allowed ? `/analytics/agents?${q}` : null);
  const chat = useData<Chatbot>(allowed ? `/analytics/chatbot?${q}` : null);
  const tools = useData<Tools>(allowed ? `/analytics/tools?${q}` : null);
  const email = useData<Email>(allowed ? `/analytics/email?${q}` : null);
  const ai = useData<Ai>(allowed ? `/analytics/ai?${q}` : null);

  const t = tickets.data;
  const r = t?.range;
  const bucket = r?.bucket ?? "day";
  const days = r?.days ?? 30;

  const sourceKeys = ["email", "chatbot_form", "conversation", "manual"];
  const aiSeries = useMemo(() => (ai.data ? ai.data.series.map((p) => ({ date: p.date, weighted: sumKeys(p, ai.data!.by_feature.map((f) => f.name)) })) : []), [ai.data]);
  const mailSeries = useMemo(
    () => (email.data ? email.data.series.map((p) => ({ date: p.date, legitimate: Number(p.legitimate) || 0, unwanted: (Number(p.spam) || 0) + (Number(p.promotion) || 0), other: sumKeys(p, ["held", "automatic", "failed"]) })) : []),
    [email.data],
  );

  if (!allowed)
    return (
      <>
        <PageHeader eyebrow="Analytics" title="Support analytics" />
        <Banner tone="neutral" title="Analytics are for workspace owners and administrators.">Ask your workspace owner for access.</Banner>
      </>
    );
  const errors = [tickets, sla, agents, chat, tools, email, ai].map((x) => x.error).filter(Boolean);

  return (
    <>
      <PageHeader eyebrow="Analytics" title="Support analytics" subtitle="Tickets, response times, SLA targets, workload, the chatbot, API tools, email and AI usage." />
      <RangePicker value={range} onChange={setRange} range={r} />
      {errors.length ? <Banner tone="danger">{errors[0]!.message}</Banner> : null}
      {!t ? (
        <Spinner />
      ) : (
        <>
          <div className="vr-grid vr-grid-4">
            <KPI label="Tickets created" value={fmt(t.totals.created)} note={delta(t.totals.created, t.previous.created, days)} />
            <KPI label="Median first response" value={minutes(t.totals.first_response_median_minutes)} note={delta(t.totals.first_response_median_minutes, t.previous.first_response_median_minutes, days)} />
            <KPI label="Median resolution" value={minutes(t.totals.resolution_median_minutes)} note={`90% within ${minutes(t.totals.resolution_p90_minutes)}`} />
            <KPI label="Reopen rate" value={pct(t.totals.reopen_rate)} note={`${fmt(t.totals.reopened)} reopened of ${fmt(t.totals.resolutions)} resolutions`} />
          </div>
          <div className="vr-grid vr-grid-4">
            <KPI label="SLA breaches" value={fmt(sla.data?.breaches ?? 0)} note={sla.data ? `${sla.data.breaching_now.length ? `${sla.data.breaching_now.length} open tickets breaching now` : "No open ticket is breaching now"}` : null} />
            <KPI label="Chatbot deflection" value={pct(chat.data?.totals.deflection_rate)} note={chat.data ? `${fmt(chat.data.totals.deflected)} of ${fmt(chat.data.totals.conversations)} conversations answered without a person` : null} />
            <KPI label="Handoff rate" value={pct(chat.data?.totals.handoff_rate)} note={chat.data ? `${fmt(chat.data.totals.handed_off)} went to the team` : null} />
            <KPI label="AI tokens" value={compact(ai.data?.totals.weighted_tokens ?? 0)} note={ai.data ? delta(ai.data.totals.weighted_tokens, ai.data.previous.weighted_tokens, days) : null} />
          </div>

          <h2 className="vr-h vr-section-title">Tickets</h2>
          <ChartCard
            title="Ticket volume by source"
            subtitle={`${fmt(t.totals.created)} tickets created`}
            chart={
              <div className="vr-grid vr-grid-2">
                {sourceKeys.map((k) => (
                  <div key={k}>
                    <b style={{ fontSize: 13 }}>
                      {SOURCE[k]} · <span className="vr-mono">{fmt(t.source_totals[k] ?? 0)}</span>
                    </b>
                    <BarChart data={t.by_source} series={[{ key: k, label: SOURCE[k], tone: "s2" }]} label={`Tickets from ${SOURCE[k]}`} bucket={bucket} height={80} />
                  </div>
                ))}
              </div>
            }
            table={seriesTable(t.by_source, sourceKeys.map((k) => ({ key: k, label: SOURCE[k], tone: "s2" })), bucket)}
          />
          <div className="vr-grid vr-grid-2">
            <ChartCard
              title="Tickets by category"
              chart={<BarList label="Tickets by category" rows={t.category_totals.map((c) => ({ name: c.name, value: c.count }))} />}
              table={{ columns: ["Category", "Tickets"], rows: t.category_totals.map((c) => [c.name, fmt(c.count)]) }}
            />
            <ChartCard
              title="Resolved and reopened"
              subtitle={`Reopen rate ${pct(t.totals.reopen_rate)}`}
              chart={
                <BarChart
                  data={t.reopens}
                  series={[
                    { key: "resolved", label: "Resolved", tone: "s3" },
                    { key: "reopened", label: "Reopened", tone: "s1" },
                  ]}
                  label="Resolutions and reopens"
                  bucket={bucket}
                />
              }
              table={seriesTable(t.reopens, [{ key: "resolved", label: "Resolved", tone: "s3" }, { key: "reopened", label: "Reopened", tone: "s1" }], bucket)}
            />
          </div>
          <div className="vr-grid vr-grid-2">
            <ChartCard
              title="First response time"
              subtitle={`Median ${minutes(t.totals.first_response_median_minutes)} · 90% within ${minutes(t.totals.first_response_p90_minutes)}`}
              chart={<LineChart data={t.response_times} serie={{ key: "first_response", label: "Median first response", tone: "s2" }} label="Median first response time" bucket={bucket} format={minutes} />}
              table={seriesTable(t.response_times, [{ key: "first_response", label: "Median first response", tone: "s2" }], bucket, minutes)}
            />
            <ChartCard
              title="Resolution time"
              subtitle={`Median ${minutes(t.totals.resolution_median_minutes)} · 90% within ${minutes(t.totals.resolution_p90_minutes)}`}
              chart={<LineChart data={t.response_times} serie={{ key: "resolution", label: "Median resolution", tone: "s2" }} label="Median resolution time" bucket={bucket} format={minutes} />}
              table={seriesTable(t.response_times, [{ key: "resolution", label: "Median resolution", tone: "s2" }], bucket, minutes)}
            />
          </div>

          {sla.data ? <SlaSection sla={sla.data} bucket={bucket} /> : null}
          {agents.data ? <AgentsSection agents={agents.data} /> : null}

          <h2 className="vr-h vr-section-title">Chatbot</h2>
          {chat.data ? (
            <div className="vr-grid vr-grid-2">
              <ChartCard
                title="Conversations by outcome"
                subtitle={`${fmt(chat.data.totals.conversations)} conversations · ${delta(chat.data.totals.conversations, chat.data.previous.conversations, days)}`}
                chart={<BarChart data={chat.data.series} series={CHAT_SERIES} label="Chatbot conversations by outcome" bucket={bucket} />}
                table={seriesTable(chat.data.series, CHAT_SERIES, bucket)}
              />
              <Card title="By chatbot" padded={false}>
                {chat.data.chatbots.length === 0 ? (
                  <EmptyState icon="chat" title="No chatbot conversations in this range" />
                ) : (
                  <div className="vr-table-wrap">
                    <table className="vr-table">
                      <thead>
                        <tr>
                          <th>Chatbot</th>
                          <th className="num">Conversations</th>
                          <th className="num">Answered without a person</th>
                          <th className="num">Went to the team</th>
                        </tr>
                      </thead>
                      <tbody>
                        {chat.data.chatbots.map((b) => (
                          <tr key={b.id}>
                            <td>{b.name}</td>
                            <td className="num">{fmt(b.conversations)}</td>
                            <td className="num">{pct(b.deflection_rate)}</td>
                            <td className="num">{pct(b.handoff_rate)}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
              </Card>
            </div>
          ) : null}

          <h2 className="vr-h vr-section-title">API tools and email</h2>
          <div className="vr-grid vr-grid-2">
            {tools.data ? (
              <ChartCard
                title="API tool calls"
                subtitle={`${fmt(tools.data.totals.calls)} calls · ${pct(tools.data.totals.success_rate)} succeeded · typical ${tools.data.totals.latency_p50_ms ?? "—"} ms, 95% within ${tools.data.totals.latency_p95_ms ?? "—"} ms`}
                chart={
                  <div className="vr-form">
                    <BarChart data={tools.data.series} series={TOOL_SERIES} label="API tool calls" bucket={bucket} />
                    <BarList label="Calls by outcome" rows={tools.data.by_status.map((s) => ({ name: CALL_WORD[s.status] ?? s.status, value: s.count, tone: s.status === "ok" ? ("s2" as const) : ("danger" as const) }))} />
                  </div>
                }
                table={{
                  columns: ["Tool", "Calls", "Succeeded", "Tests", "Typical ms", "95% ms"],
                  rows: tools.data.tools.map((x) => [x.name, fmt(x.calls), pct(x.success_rate), fmt(x.tests), x.latency_p50_ms ?? "—", x.latency_p95_ms ?? "—"]),
                }}
              />
            ) : null}
            {email.data ? (
              <ChartCard
                title="Incoming email"
                subtitle={`${fmt(email.data.totals.received)} received · ${pct(email.data.totals.spam_rate)} spam or promotions · ${fmt(email.data.totals.marked_not_spam)} marked not spam`}
                chart={
                  <div className="vr-form">
                    <BarChart data={mailSeries} series={MAIL_SERIES} label="Incoming email" bucket={bucket} />
                    <BarList
                      label="Incoming email by verdict"
                      rows={[
                        { name: "Became tickets or replies", value: email.data.totals.legitimate },
                        { name: "Spam", value: email.data.totals.spam },
                        { name: "Promotions", value: email.data.totals.promotion },
                        { name: "Held (per-sender limit)", value: email.data.totals.held },
                        { name: "Automatic (bounces, out of office, loops)", value: email.data.totals.automatic },
                        { name: "Couldn’t be processed", value: email.data.totals.failed, tone: "danger" as const },
                      ].filter((x) => x.value > 0)}
                    />
                  </div>
                }
                table={seriesTable(mailSeries, MAIL_SERIES, bucket)}
              />
            ) : null}
          </div>

          <h2 className="vr-h vr-section-title">AI usage</h2>
          {ai.data ? (
            <div className="vr-grid vr-grid-2">
              <ChartCard
                title="Weighted AI tokens"
                subtitle={`${fmt(ai.data.totals.weighted_tokens)} weighted · ${fmt(ai.data.totals.calls)} requests`}
                chart={<BarChart data={aiSeries} series={[{ key: "weighted", label: "Weighted tokens", tone: "s2" }]} label="Weighted AI tokens" bucket={bucket} format={compact} />}
                table={seriesTable(aiSeries, [{ key: "weighted", label: "Weighted tokens", tone: "s2" }], bucket)}
              />
              <ChartCard
                title="By feature and model"
                chart={
                  <div className="vr-form">
                    <BarList label="Weighted tokens by feature" rows={ai.data.by_feature.map((f) => ({ name: OPS[f.name] ?? f.name, value: f.weighted_tokens }))} format={compact} />
                    <BarList label="Weighted tokens by model" rows={ai.data.by_model.map((f) => ({ name: f.name, value: f.weighted_tokens }))} format={compact} tone="s1" />
                  </div>
                }
                table={{
                  columns: ["Feature or model", "Requests", "Input", "Output", "Weighted"],
                  rows: [...ai.data.by_feature.map((f) => ({ ...f, name: OPS[f.name] ?? f.name })), ...ai.data.by_model].map((f) => [f.name, fmt(f.calls), fmt(f.input_tokens), fmt(f.output_tokens), fmt(f.weighted_tokens)]),
                }}
              />
            </div>
          ) : null}
        </>
      )}
    </>
  );
}

const CHAT_SERIES: Series[] = [
  { key: "deflected", label: "Answered without a person", tone: "s1" },
  { key: "handed_off", label: "Went to the team", tone: "s2" },
  { key: "unanswered", label: "No answer", tone: "s3" },
];
const TOOL_SERIES: Series[] = [
  { key: "ok", label: "Succeeded", tone: "s2" },
  { key: "failed", label: "Failed", tone: "danger" },
];
const MAIL_SERIES: Series[] = [
  { key: "legitimate", label: "Tickets and replies", tone: "s1" },
  { key: "unwanted", label: "Spam and promotions", tone: "s3" },
  { key: "other", label: "Held, automatic or failed", tone: "muted" },
];

function SlaSection({ sla, bucket }: { sla: Sla; bucket: "day" | "week" }) {
  const series: Series[] = [
    { key: "response_breached", label: "First response late", tone: "warn" },
    { key: "resolution_breached", label: "Resolution late", tone: "danger" },
  ];
  return (
    <>
      <h2 className="vr-h vr-section-title">SLA targets</h2>
      <Card
        title="Within target, by priority"
        padded={false}
        actions={
          <Link className="vr-btn vr-btn-ghost vr-btn-sm" href="/settings/tickets">
            Change targets
          </Link>
        }
      >
        <div className="vr-table-wrap">
          <table className="vr-table">
            <thead>
              <tr>
                <th>Priority</th>
                <th className="num">Tickets</th>
                <th>First response target</th>
                <th className="num">Answered in time</th>
                <th>Resolution target</th>
                <th className="num">Resolved in time</th>
              </tr>
            </thead>
            <tbody>
              {sla.by_priority.map((p) => (
                <tr key={p.priority} className="vr-sla-row">
                  <td>{PRIORITY[p.priority]}</td>
                  <td className="num">{fmt(p.tickets)}</td>
                  <td>{minutes(p.targets.first_response_minutes)}</td>
                  <td className="num">
                    {pct(p.response_met_rate)}
                    {p.response_breached ? <span className="vr-word-danger"> · {fmt(p.response_breached)} breached</span> : null}
                    {p.response_pending ? <span className="vr-muted"> · {fmt(p.response_pending)} still in time</span> : null}
                  </td>
                  <td>{minutes(p.targets.resolution_minutes)}</td>
                  <td className="num">
                    {pct(p.resolution_met_rate)}
                    {p.resolution_breached ? <span className="vr-word-danger"> · {fmt(p.resolution_breached)} breached</span> : null}
                    {p.resolution_pending ? <span className="vr-muted"> · {fmt(p.resolution_pending)} still in time</span> : null}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>
      <div className="vr-grid vr-grid-2">
        <ChartCard
          title="Breaches over time"
          subtitle="By the day the ticket was created"
          chart={<BarChart data={sla.series} series={series} label="SLA breaches" bucket={bucket} />}
          table={seriesTable(sla.series, series, bucket)}
        />
        <Card title="Open tickets breaching now" padded={false}>
          {sla.breaching_now.length === 0 ? (
            <EmptyState icon="check" title="No open ticket is past its target" />
          ) : (
            <div className="vr-table-wrap">
              <table className="vr-table">
                <thead>
                  <tr>
                    <th>Ticket</th>
                    <th>Priority</th>
                    <th>Late on</th>
                    <th className="num">Overdue by</th>
                  </tr>
                </thead>
                <tbody>
                  {sla.breaching_now.map((x) => (
                    <tr key={x.id}>
                      <td>
                        <Link href={`/tickets/${x.id}`}>
                          #{x.number} {x.subject}
                        </Link>
                        <div className="vr-muted vr-caption">{x.assignee ?? "Unassigned"}</div>
                      </td>
                      <td>{PRIORITY[x.priority]}</td>
                      <td className="vr-word-danger">{x.target === "first_response" ? "First response" : "Resolution"}</td>
                      <td className="num">{minutes(x.overdue_minutes)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Card>
      </div>
    </>
  );
}

function AgentsSection({ agents }: { agents: Agents }) {
  return (
    <>
      <h2 className="vr-h vr-section-title">Agent workload</h2>
      <Card title="People who work tickets" padded={false} actions={<span className="vr-help">{fmt(agents.unassigned_open)} open tickets unassigned</span>}>
        <div className="vr-table-wrap">
          <table className="vr-table">
            <thead>
              <tr>
                <th>Member</th>
                <th>Availability</th>
                <th className="num">Open now</th>
                <th className="num">Assigned</th>
                <th className="num">Ticket replies</th>
                <th className="num">Chat replies</th>
                <th className="num">Resolved</th>
                <th className="num">Median first response</th>
              </tr>
            </thead>
            <tbody>
              {agents.members.map((m) => (
                <tr key={m.id}>
                  <td>
                    {m.name}
                    <div className="vr-muted vr-caption">{m.role}</div>
                  </td>
                  <td>
                    <span className="vr-avail">
                      <span className={m.availability === "away" ? "vr-avail-dot is-away" : "vr-avail-dot"} aria-hidden="true" />
                      {m.availability === "away" ? "Away" : "Available"}
                    </span>
                  </td>
                  <td className="num">
                    {fmt(m.open_now)}
                    {m.max_open ? <span className="vr-muted"> of {m.max_open}</span> : null}
                  </td>
                  <td className="num">{fmt(m.assigned)}</td>
                  <td className="num">{fmt(m.replies)}</td>
                  <td className="num">{fmt(m.chat_replies)}</td>
                  <td className="num">{fmt(m.resolved)}</td>
                  <td className="num">{minutes(m.first_response_median_minutes)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>
    </>
  );
}
