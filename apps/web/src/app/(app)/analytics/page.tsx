"use client";

import { t as tr, msg, tp } from "@/i18n";
import Link from "next/link";
import { useMemo, useState } from "react";

import { BarChart, BarList, ChartCard, delta, KPI, LineChart, minutes, pct, type Point, type Series, seriesTable } from "@/components/charts";
import { RangePicker, rangeQuery, type RangeOut } from "@/components/range";
import { CALL_WORD } from "@/components/tools";
import { Banner, Card, compact, EmptyState, fmt, PageHeader, Spinner } from "@/components/ui";
import type { ToolCallStatus } from "@/lib/api";
import { useSession } from "@/lib/session";
import { SOURCE } from "@/lib/tickets";
import { opLabel } from "@/lib/usage";
import { useData } from "@/lib/use-data";
import { roleName } from "@/lib/roles";

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
  totals: { received: number; legitimate: number; spam: number; promotion: number; held: number; automatic: number; failed: number; marked_not_spam: number; decided_by_ai: number; caught_by_model: number; possible_spam: number; spam_rate: number | null };
  series: Point[];
};
type Usage = { name: string; input_tokens: number; output_tokens: number; weighted_tokens: number; calls: number };
type Ai = { totals: { weighted_tokens: number; calls: number; input_tokens: number; output_tokens: number }; previous: { weighted_tokens: number }; series: Point[]; by_feature: Usage[]; by_model: Usage[] };

const PRIORITY: Record<string, string> = { urgent: msg("Urgent"), high: msg("High"), normal: msg("Normal"), low: msg("Low") };

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
        <PageHeader eyebrow={tr("Analytics")} title={tr("Support analytics")} />
        <Banner tone="neutral" title={tr("Analytics are for workspace owners and administrators.")}>{tr("Ask your workspace owner for access.")}</Banner>
      </>
    );
  const errors = [tickets, sla, agents, chat, tools, email, ai].map((x) => x.error).filter(Boolean);

  return (
    <>
      <PageHeader eyebrow={tr("Analytics")} title={tr("Support analytics")} subtitle={tr("Tickets, response times, SLA targets, workload, the chatbot, API tools, email and AI usage.")} />
      <RangePicker value={range} onChange={setRange} range={r} />
      {errors.length ? <Banner tone="danger">{errors[0]!.message}</Banner> : null}
      {!t ? (
        <Spinner />
      ) : (
        <>
          <div className="vr-grid vr-grid-4">
            <KPI label={tr("Tickets created")} value={fmt(t.totals.created)} note={delta(t.totals.created, t.previous.created, days)} />
            <KPI label={tr("Median first response")} value={minutes(t.totals.first_response_median_minutes)} note={delta(t.totals.first_response_median_minutes, t.previous.first_response_median_minutes, days)} />
            <KPI label={tr("Median resolution")} value={minutes(t.totals.resolution_median_minutes)} note={tr("90% within {resolution_p90_minutes}", { resolution_p90_minutes: minutes(t.totals.resolution_p90_minutes) })} />
            <KPI label={tr("Reopen rate")} value={pct(t.totals.reopen_rate)} note={tr("{reopened} reopened of {resolutions} resolutions", { reopened: fmt(t.totals.reopened), resolutions: fmt(t.totals.resolutions) })} />
          </div>
          <div className="vr-grid vr-grid-4">
            <KPI label={tr("SLA breaches")} value={fmt(sla.data?.breaches ?? 0)} note={sla.data ? (sla.data.breaching_now.length ? tp("{n} open ticket breaching now", "{n} open tickets breaching now", sla.data.breaching_now.length) : tr("No open ticket is breaching now")) : null} />
            <KPI label={tr("Chatbot deflection")} value={pct(chat.data?.totals.deflection_rate)} note={chat.data ? tr("{deflected} of {conversations} conversations answered without a person", { deflected: fmt(chat.data.totals.deflected), conversations: fmt(chat.data.totals.conversations) }) : null} />
            <KPI label={tr("Handoff rate")} value={pct(chat.data?.totals.handoff_rate)} note={chat.data ? tr("{handed_off} went to the team", { handed_off: fmt(chat.data.totals.handed_off) }) : null} />
            <KPI label={tr("AI tokens")} value={compact(ai.data?.totals.weighted_tokens ?? 0)} note={ai.data ? delta(ai.data.totals.weighted_tokens, ai.data.previous.weighted_tokens, days) : null} />
          </div>

          <h2 className="vr-h vr-section-title">{tr("Tickets")}</h2>
          <ChartCard
            title={tr("Ticket volume by source")}
            subtitle={tr("{created} tickets created", { created: fmt(t.totals.created) })}
            chart={
              <div className="vr-grid vr-grid-2">
                {sourceKeys.map((k) => (
                  <div key={k}>
                    <b style={{ fontSize: 13 }}>
                      {tr(SOURCE[k])} · <span className="vr-mono">{fmt(t.source_totals[k] ?? 0)}</span>
                    </b>
                    <BarChart data={t.by_source} series={[{ key: k, label: tr(SOURCE[k]), tone: "s2" }]} label={tr("Tickets from {v}", { v: SOURCE[k] })} bucket={bucket} height={80} />
                  </div>
                ))}
              </div>
            }
            table={seriesTable(t.by_source, sourceKeys.map((k) => ({ key: k, label: tr(SOURCE[k]), tone: "s2" })), bucket)}
          />
          <div className="vr-grid vr-grid-2">
            <ChartCard
              title={tr("Tickets by category")}
              chart={<BarList label={tr("Tickets by category")} rows={t.category_totals.map((c) => ({ name: c.name, value: c.count }))} />}
              table={{ columns: ["Category", "Tickets"], rows: t.category_totals.map((c) => [c.name, fmt(c.count)]) }}
            />
            <ChartCard
              title={tr("Resolved and reopened")}
              subtitle={tr("Reopen rate {reopen_rate}", { reopen_rate: pct(t.totals.reopen_rate) })}
              chart={
                <BarChart
                  data={t.reopens}
                  series={[
                    { key: "resolved", label: tr("Resolved"), tone: "s3" },
                    { key: "reopened", label: tr("Reopened"), tone: "s1" },
                  ]}
                  label={tr("Resolutions and reopens")}
                  bucket={bucket}
                />
              }
              table={seriesTable(t.reopens, [{ key: "resolved", label: tr("Resolved"), tone: "s3" }, { key: "reopened", label: tr("Reopened"), tone: "s1" }], bucket)}
            />
          </div>
          <div className="vr-grid vr-grid-2">
            <ChartCard
              title={tr("First response time")}
              subtitle={tr("Median {first_response_median_minutes} · 90% within {first_response_p90_minutes}", { first_response_median_minutes: minutes(t.totals.first_response_median_minutes), first_response_p90_minutes: minutes(t.totals.first_response_p90_minutes) })}
              chart={<LineChart data={t.response_times} serie={{ key: "first_response", label: tr("Median first response"), tone: "s2" }} label={tr("Median first response time")} bucket={bucket} format={minutes} />}
              table={seriesTable(t.response_times, [{ key: "first_response", label: tr("Median first response"), tone: "s2" }], bucket, minutes)}
            />
            <ChartCard
              title={tr("Resolution time")}
              subtitle={tr("Median {resolution_median_minutes} · 90% within {resolution_p90_minutes}", { resolution_median_minutes: minutes(t.totals.resolution_median_minutes), resolution_p90_minutes: minutes(t.totals.resolution_p90_minutes) })}
              chart={<LineChart data={t.response_times} serie={{ key: "resolution", label: tr("Median resolution"), tone: "s2" }} label={tr("Median resolution time")} bucket={bucket} format={minutes} />}
              table={seriesTable(t.response_times, [{ key: "resolution", label: tr("Median resolution"), tone: "s2" }], bucket, minutes)}
            />
          </div>

          {sla.data ? <SlaSection sla={sla.data} bucket={bucket} /> : null}
          {agents.data ? <AgentsSection agents={agents.data} /> : null}

          <h2 className="vr-h vr-section-title">{tr("Chatbot")}</h2>
          {chat.data ? (
            <div className="vr-grid vr-grid-2">
              <ChartCard
                title={tr("Conversations by outcome")}
                subtitle={tr("{conversations} conversations · {conversations2}", { conversations: fmt(chat.data.totals.conversations), conversations2: delta(chat.data.totals.conversations, chat.data.previous.conversations, days) })}
                chart={<BarChart data={chat.data.series} series={CHAT_SERIES} label={tr("Chatbot conversations by outcome")} bucket={bucket} />}
                table={seriesTable(chat.data.series, CHAT_SERIES, bucket)}
              />
              <Card title={tr("By chatbot")} padded={false}>
                {chat.data.chatbots.length === 0 ? (
                  <EmptyState icon="chat" title={tr("No chatbot conversations in this range")} />
                ) : (
                  <div className="vr-table-wrap">
                    <table className="vr-table">
                      <thead>
                        <tr>
                          <th>{tr("Chatbot")}</th>
                          <th className="num">{tr("Conversations")}</th>
                          <th className="num">{tr("Answered without a person")}</th>
                          <th className="num">{tr("Went to the team")}</th>
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

          <h2 className="vr-h vr-section-title">{tr("API tools and email")}</h2>
          <div className="vr-grid vr-grid-2">
            {tools.data ? (
              <ChartCard
                title={tr("API tool calls")}
                subtitle={tr("{calls} calls · {success_rate} succeeded · typical {v} ms, 95% within {v2} ms", { calls: fmt(tools.data.totals.calls), success_rate: pct(tools.data.totals.success_rate), v: tools.data.totals.latency_p50_ms ?? "—", v2: tools.data.totals.latency_p95_ms ?? "—" })}
                chart={
                  <div className="vr-form">
                    <BarChart data={tools.data.series} series={TOOL_SERIES} label={tr("API tool calls")} bucket={bucket} />
                    <BarList label={tr("Calls by outcome")} rows={tools.data.by_status.map((s) => ({ name: CALL_WORD[s.status] ?? s.status, value: s.count, tone: s.status === "ok" ? ("s2" as const) : ("danger" as const) }))} />
                  </div>
                }
                table={{
                  columns: ["Tool", "Calls", "Succeeded", "Tests", tr("Typical ms"), "95% ms"],
                  rows: tools.data.tools.map((x) => [x.name, fmt(x.calls), pct(x.success_rate), fmt(x.tests), x.latency_p50_ms ?? "—", x.latency_p95_ms ?? "—"]),
                }}
              />
            ) : null}
            {email.data ? (
              <ChartCard
                title={tr("Incoming email")}
                subtitle={tr("{received} received · {spam_rate} spam or promotions · {marked_not_spam} marked not spam · {caught} caught by the spam filter without AI · {possible} tickets marked possible spam", {
                  received: fmt(email.data.totals.received),
                  spam_rate: pct(email.data.totals.spam_rate),
                  marked_not_spam: fmt(email.data.totals.marked_not_spam),
                  caught: fmt(email.data.totals.caught_by_model),
                  possible: fmt(email.data.totals.possible_spam),
                })}
                chart={
                  <div className="vr-form">
                    <BarChart data={mailSeries} series={MAIL_SERIES} label={tr("Incoming email")} bucket={bucket} />
                    <BarList
                      label={tr("Incoming email by verdict")}
                      rows={[
                        { name: tr("Became tickets or replies"), value: email.data.totals.legitimate },
                        { name: tr("Spam"), value: email.data.totals.spam },
                        { name: tr("Promotions"), value: email.data.totals.promotion },
                        { name: tr("Held (per-sender limit)"), value: email.data.totals.held },
                        { name: tr("Automatic (bounces, out of office, loops)"), value: email.data.totals.automatic },
                        { name: tr("Couldn’t be processed"), value: email.data.totals.failed, tone: "danger" as const },
                      ].filter((x) => x.value > 0)}
                    />
                  </div>
                }
                table={seriesTable(mailSeries, MAIL_SERIES, bucket)}
              />
            ) : null}
          </div>

          <h2 className="vr-h vr-section-title">{tr("AI usage")}</h2>
          {ai.data ? (
            <div className="vr-grid vr-grid-2">
              <ChartCard
                title={tr("Weighted AI tokens")}
                subtitle={tr("{weighted_tokens} weighted · {calls} requests", { weighted_tokens: fmt(ai.data.totals.weighted_tokens), calls: fmt(ai.data.totals.calls) })}
                chart={<BarChart data={aiSeries} series={[{ key: "weighted", label: tr("Weighted tokens"), tone: "s2" }]} label={tr("Weighted AI tokens")} bucket={bucket} format={compact} />}
                table={seriesTable(aiSeries, [{ key: "weighted", label: tr("Weighted tokens"), tone: "s2" }], bucket)}
              />
              <ChartCard
                title={tr("By feature and model")}
                chart={
                  <div className="vr-form">
                    <BarList label={tr("Weighted tokens by feature")} rows={ai.data.by_feature.map((f) => ({ name: opLabel(f.name), value: f.weighted_tokens }))} format={compact} />
                    <BarList label={tr("Weighted tokens by model")} rows={ai.data.by_model.map((f) => ({ name: f.name, value: f.weighted_tokens }))} format={compact} tone="s1" />
                  </div>
                }
                table={{
                  columns: [tr("Feature or model"), "Requests", "Input", "Output", "Weighted"],
                  rows: [...ai.data.by_feature.map((f) => ({ ...f, name: opLabel(f.name) })), ...ai.data.by_model].map((f) => [f.name, fmt(f.calls), fmt(f.input_tokens), fmt(f.output_tokens), fmt(f.weighted_tokens)]),
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
  { key: "deflected", label: msg("Answered without a person"), tone: "s1" },
  { key: "handed_off", label: msg("Went to the team"), tone: "s2" },
  { key: "unanswered", label: msg("No answer"), tone: "s3" },
];
const TOOL_SERIES: Series[] = [
  { key: "ok", label: msg("Succeeded"), tone: "s2" },
  { key: "failed", label: msg("Failed"), tone: "danger" },
];
const MAIL_SERIES: Series[] = [
  { key: "legitimate", label: msg("Tickets and replies"), tone: "s1" },
  { key: "unwanted", label: msg("Spam and promotions"), tone: "s3" },
  { key: "other", label: msg("Held, automatic or failed"), tone: "muted" },
];

function SlaSection({ sla, bucket }: { sla: Sla; bucket: "day" | "week" }) {
  const series: Series[] = [
    { key: "response_breached", label: tr("First response late"), tone: "warn" },
    { key: "resolution_breached", label: tr("Resolution late"), tone: "danger" },
  ];
  return (
    <>
      <h2 className="vr-h vr-section-title">{tr("SLA targets")}</h2>
      <Card
        title={tr("Within target, by priority")}
        padded={false}
        actions={
          <Link className="vr-btn vr-btn-ghost vr-btn-sm" href="/settings/tickets">
            {tr("Change targets")}
          </Link>
        }
      >
        <div className="vr-table-wrap">
          <table className="vr-table">
            <thead>
              <tr>
                <th>{tr("Priority")}</th>
                <th className="num">{tr("Tickets")}</th>
                <th>{tr("First response target")}</th>
                <th className="num">{tr("Answered in time")}</th>
                <th>{tr("Resolution target")}</th>
                <th className="num">{tr("Resolved in time")}</th>
              </tr>
            </thead>
            <tbody>
              {sla.by_priority.map((p) => (
                <tr key={p.priority} className="vr-sla-row">
                  <td>{tr(PRIORITY[p.priority])}</td>
                  <td className="num">{fmt(p.tickets)}</td>
                  <td>{minutes(p.targets.first_response_minutes)}</td>
                  <td className="num">
                    {pct(p.response_met_rate)}
                    {p.response_breached ? <span className="vr-word-danger"> · {fmt(p.response_breached)} {tr("breached")}</span> : null}
                    {p.response_pending ? <span className="vr-muted"> · {fmt(p.response_pending)} {tr("still in time")}</span> : null}
                  </td>
                  <td>{minutes(p.targets.resolution_minutes)}</td>
                  <td className="num">
                    {pct(p.resolution_met_rate)}
                    {p.resolution_breached ? <span className="vr-word-danger"> · {fmt(p.resolution_breached)} {tr("breached")}</span> : null}
                    {p.resolution_pending ? <span className="vr-muted"> · {fmt(p.resolution_pending)} {tr("still in time")}</span> : null}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>
      <div className="vr-grid vr-grid-2">
        <ChartCard
          title={tr("Breaches over time")}
          subtitle={tr("By the day the ticket was created")}
          chart={<BarChart data={sla.series} series={series} label={tr("SLA breaches")} bucket={bucket} />}
          table={seriesTable(sla.series, series, bucket)}
        />
        <Card title={tr("Open tickets breaching now")} padded={false}>
          {sla.breaching_now.length === 0 ? (
            <EmptyState icon="check" title={tr("No open ticket is past its target")} />
          ) : (
            <div className="vr-table-wrap">
              <table className="vr-table">
                <thead>
                  <tr>
                    <th>{tr("Ticket")}</th>
                    <th>{tr("Priority")}</th>
                    <th>{tr("Late on")}</th>
                    <th className="num">{tr("Overdue by")}</th>
                  </tr>
                </thead>
                <tbody>
                  {sla.breaching_now.map((x) => (
                    <tr key={x.id}>
                      <td>
                        <Link href={`/tickets/${x.id}`}>
                          #{x.number} {x.subject}
                        </Link>
                        <div className="vr-muted vr-caption">{x.assignee ?? tr("Unassigned")}</div>
                      </td>
                      <td>{tr(PRIORITY[x.priority])}</td>
                      <td className="vr-word-danger">{x.target === "first_response" ? tr("First response") : tr("Resolution")}</td>
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
      <h2 className="vr-h vr-section-title">{tr("Agent workload")}</h2>
      <Card title={tr("People who work tickets")} padded={false} actions={<span className="vr-help">{fmt(agents.unassigned_open)} {tr("open tickets unassigned")}</span>}>
        <div className="vr-table-wrap">
          <table className="vr-table">
            <thead>
              <tr>
                <th>{tr("Member")}</th>
                <th>{tr("Availability")}</th>
                <th className="num">{tr("Open now")}</th>
                <th className="num">{tr("Assigned")}</th>
                <th className="num">{tr("Ticket replies")}</th>
                <th className="num">{tr("Chat replies")}</th>
                <th className="num">{tr("Resolved")}</th>
                <th className="num">{tr("Median first response")}</th>
              </tr>
            </thead>
            <tbody>
              {agents.members.map((m) => (
                <tr key={m.id}>
                  <td>
                    {m.name}
                    <div className="vr-muted vr-caption">{roleName(m.role)}</div>
                  </td>
                  <td>
                    <span className="vr-avail">
                      <span className={m.availability === "away" ? "vr-avail-dot is-away" : "vr-avail-dot"} aria-hidden="true" />
                      {m.availability === "away" ? tr("Away") : tr("Available")}
                    </span>
                  </td>
                  <td className="num">
                    {fmt(m.open_now)}
                    {m.max_open ? <span className="vr-muted"> {tr("of")} {m.max_open}</span> : null}
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
