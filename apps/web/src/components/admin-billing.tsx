"use client";

import { t, msg } from "@/i18n";
/* System admin: token packages, platform reports (usage, provider costs, subscriptions, MRR and revenue, failed
   payments) and the payment provider's recent events. Money is always shown per currency, never added up. */

import { useState } from "react";

import { BarChart, BarList, ChartCard, delta, KPI, type Series, seriesTable } from "@/components/charts";
import { RangePicker, rangeQuery, type RangeOut } from "@/components/range";
import { Badge, Banner, Button, Card, compact, date, dateTime, EmptyState, Field, fmt, money, Spinner, Switch, type Tone } from "@/components/ui";
import { api, errorText } from "@/lib/api";
import { opLabel } from "@/lib/usage";
import { useData } from "@/lib/use-data";

type Pkg = { id: string; key: string; name: string; description: string; tokens: number; price_cents: number; currency: string; is_public: boolean; is_active: boolean; sort_order: number };

function PackageEditor({ pkg, onDone }: { pkg: Pkg | null; onDone: () => void }) {
  const [v, setV] = useState({
    key: pkg?.key ?? "",
    name: pkg?.name ?? "",
    description: pkg?.description ?? "",
    tokens: String(pkg?.tokens ?? 1_000_000),
    price: String((pkg?.price_cents ?? 1000) / 100),
    currency: pkg?.currency ?? "USD",
    is_public: pkg?.is_public ?? true,
    is_active: pkg?.is_active ?? true,
  });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  async function save() {
    setBusy(true);
    setError(null);
    const body = {
      name: v.name,
      description: v.description,
      tokens: Number(v.tokens),
      price_cents: Math.round(Number(v.price) * 100),
      currency: v.currency.toUpperCase(),
      is_public: v.is_public,
      is_active: v.is_active,
    };
    try {
      if (pkg) await api.patch(`/admin/token-packages/${pkg.id}`, body);
      else await api.post("/admin/token-packages", { ...body, key: v.key });
      onDone();
    } catch (e) {
      setError(errorText(e));
      setBusy(false);
    }
  }
  return (
    <Card title={pkg ? t("Edit {name}", { name: pkg.name }) : t("New token package")}>
      <div className="vr-form">
        {error ? <Banner tone="danger">{error}</Banner> : null}
        <div className="vr-form-row">
          <Field label={t("Name")} value={v.name} onChange={(e) => setV({ ...v, name: e.target.value })} />
          <Field label={t("Key")} help={t("Lowercase letters, digits and dashes. Can’t change later.")} disabled={!!pkg} value={v.key} onChange={(e) => setV({ ...v, key: e.target.value })} />
        </div>
        <Field label={t("Description")} value={v.description} onChange={(e) => setV({ ...v, description: e.target.value })} />
        <div className="vr-form-row">
          <Field label={t("Weighted tokens")} type="number" min={1000} value={v.tokens} onChange={(e) => setV({ ...v, tokens: e.target.value })} />
          <Field label={t("Price (before tax)")} type="number" min={0.5} step="0.01" value={v.price} onChange={(e) => setV({ ...v, price: e.target.value })} />
          <Field label={t("Currency")} help={t("Workspaces buy only in the currency they pay in.")} value={v.currency} maxLength={3} onChange={(e) => setV({ ...v, currency: e.target.value.toUpperCase() })} />
        </div>
        <Switch label={t("On sale")} help={t("Shown to workspaces on Billing → Token packages.")} checked={v.is_public && v.is_active} onChange={(on) => setV({ ...v, is_public: on, is_active: on })} />
        <span className="vr-help">{t("Bought tokens carry over between periods and expire 12 months after purchase. Price changes apply to new purchases only.")}</span>
        <div className="vr-actions">
          <Button variant="primary" busy={busy} onClick={save}>
            {pkg ? t("Save package") : t("Create package")}
          </Button>
          <Button variant="ghost" onClick={onDone}>
            {t("Cancel")}
          </Button>
        </div>
      </div>
    </Card>
  );
}

export function TokenPackagesAdmin() {
  const list = useData<Pkg[]>("/admin/token-packages");
  const [editing, setEditing] = useState<Pkg | "new" | null>(null);
  if (editing)
    return (
      <PackageEditor
        pkg={editing === "new" ? null : editing}
        onDone={() => {
          setEditing(null);
          list.reload();
        }}
      />
    );
  return (
    <Card title={t("Token packages")} padded={false} actions={<Button size="sm" variant="primary" icon="plus" onClick={() => setEditing("new")}>{t("New package")}</Button>}>
      {list.loading && !list.data ? (
        <Spinner />
      ) : !list.data?.length ? (
        <EmptyState icon="spark" title={t("No token packages")} />
      ) : (
        <div className="vr-table-wrap">
          <table className="vr-table">
            <thead>
              <tr>
                <th>{t("Package")}</th>
                <th className="num">{t("Tokens")}</th>
                <th className="num">{t("Price")}</th>
                <th>{t("Status")}</th>
                <th>
                  <span className="sr-only">{t("Actions")}</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {list.data.map((p) => (
                <tr key={p.id}>
                  <td>
                    <b style={{ fontWeight: 500 }}>{p.name}</b> <span className="vr-mono vr-muted" style={{ fontSize: 12 }}>{p.key}</span>
                  </td>
                  <td className="num">{compact(p.tokens)}</td>
                  <td className="num">{money(p.price_cents, p.currency)}</td>
                  <td>{p.is_active && p.is_public ? <Badge tone="brand" dot>{t("On sale")}</Badge> : <Badge dot>{t("Not on sale")}</Badge>}</td>
                  <td style={{ textAlign: "right" }}>
                    <Button size="sm" variant="ghost" onClick={() => setEditing(p)}>
                      {t("Edit")}
                    </Button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Card>
  );
}

// ---- Reports ------------------------------------------------------------------------------------------------

type Revenue = {
  range: RangeOut;
  currencies: string[];
  mrr_now: { currency: string; mrr_cents: number; subscriptions: number }[];
  mrr_series: Record<string, number | string>[];
  revenue: Record<string, number>;
  revenue_previous: Record<string, number>;
  revenue_series: Record<string, number | string>[];
  by_kind: { currency: string; kind: string; net_cents: number; invoices: number }[];
};
type Costs = { total_usd: number; previous_usd: number; series: { date: string; cost: number }[]; by_model: { name: string; cost_usd: number }[]; by_feature: { name: string; cost_usd: number }[]; usd_margin_rate: number | null };
type Subs = { now: { active: number; past_due: number; ended: number }; by_plan: { plan: string; currency: string; count: number }[]; series: Record<string, number | string>[]; totals: { new: number; upgraded: number; canceled: number } };
type Failed = {
  totals: { failed: number; recovered: number };
  series: Record<string, number | string>[];
  at_risk: { currency: string; amount_cents: number; invoices: number }[];
  past_due: { workspace_id: string; workspace: string; plan: string; since: string; next_retry_at: string | null; retries: number; amount_cents: number | null; currency: string | null }[];
};
type Usage = { active_workspaces: number; workspaces: { id: string; name: string; plan: string | null; subscription_status: string | null; weighted_tokens: number; provider_cost_usd: number; tickets: number; conversations: number; members: number }[] };

const KIND_WORD: Record<string, string> = { subscription: msg("New subscriptions"), renewal: msg("Renewals"), upgrade: msg("Upgrades"), package: msg("Token packages") };
const usd = (n: number) => `$${n.toFixed(n < 10 ? 2 : 0)}`;

export function PlatformReports() {
  const [range, setRange] = useState({ preset: "30d", from: "", to: "" });
  const q = rangeQuery(range);
  const rev = useData<Revenue>(`/admin/analytics/revenue?${q}`);
  const costs = useData<Costs>(`/admin/analytics/costs?${q}`);
  const subs = useData<Subs>(`/admin/analytics/subscriptions?${q}`);
  const failed = useData<Failed>(`/admin/analytics/failed-payments?${q}`);
  const usage = useData<Usage>(`/admin/analytics/usage?${q}`);
  const r = rev.data?.range;
  const bucket = r?.bucket ?? "day";
  const days = r?.days ?? 30;
  const err = [rev, costs, subs, failed, usage].map((x) => x.error).find(Boolean);
  const subSeries: Series[] = [
    { key: "new", label: t("New"), tone: "s1" },
    { key: "upgraded", label: t("Upgraded"), tone: "s2" },
    { key: "canceled", label: t("Ended"), tone: "s3" },
  ];
  const failSeries: Series[] = [
    { key: "recovered", label: t("Recovered on retry"), tone: "s2" },
    { key: "failed", label: t("Still failed"), tone: "danger" },
  ];
  return (
    <div className="vr-stack" style={{ padding: 0 }}>
      <RangePicker value={range} onChange={setRange} range={r} />
      {err ? <Banner tone="danger">{err.message}</Banner> : null}
      {!rev.data ? (
        <Spinner />
      ) : (
        <>
          <div className="vr-grid vr-grid-4">
            {rev.data.mrr_now.length ? (
              rev.data.mrr_now.map((m) => <KPI key={m.currency} label={t("MRR · {currency}", { currency: m.currency })} value={money(m.mrr_cents, m.currency)} note={t("{subscriptions} paying workspaces", { subscriptions: fmt(m.subscriptions) })} />)
            ) : (
              <KPI label={t("MRR")} value="—" note={t("No paying workspaces yet")} />
            )}
            {rev.data.currencies.map((c) => (
              <KPI key={`rev-${c}`} label={t("Revenue · {c}", { c: c })} value={money(rev.data!.revenue[c] ?? 0, c)} note={t("Net of tax and refunds · {delta}", { delta: delta(rev.data!.revenue[c] ?? 0, rev.data!.revenue_previous[c] ?? 0, days) })} />
            ))}
            {costs.data ? <KPI label={t("AI provider cost · USD")} value={usd(costs.data.total_usd)} note={costs.data.usd_margin_rate === null ? delta(costs.data.total_usd, costs.data.previous_usd, days) : t("{usd_margin_rate}% margin on USD revenue", { usd_margin_rate: costs.data.usd_margin_rate })} /> : null}
            {failed.data ? <KPI label={t("Failed payments")} value={fmt(failed.data.totals.failed)} note={t("{recovered} recovered on retry · {length} past due now", { recovered: fmt(failed.data.totals.recovered), length: fmt(failed.data.past_due.length) })} /> : null}
          </div>

          <div className="vr-grid vr-grid-2">
            {rev.data.currencies.map((c) => {
              const s: Series[] = [{ key: c, label: t("MRR in {c}", { c: c }), tone: "s2" }];
              return (
                <ChartCard
                  key={`mrr-${c}`}
                  title={t("MRR · {c}", { c: c })}
                  subtitle={t("At the end of each day or week")}
                  chart={<BarChart data={rev.data!.mrr_series as never} series={s} label={t("Monthly recurring revenue in {c}", { c: c })} bucket={bucket} format={(n) => money(n, c)} />}
                  table={seriesTable(rev.data!.mrr_series as never, s, bucket, (n) => money(n, c))}
                />
              );
            })}
            {rev.data.currencies.map((c) => {
              const s: Series[] = [{ key: c, label: t("Revenue in {c}", { c: c }), tone: "s1" }];
              return (
                <ChartCard
                  key={`rv-${c}`}
                  title={t("Revenue · {c}", { c: c })}
                  subtitle={t("Invoices paid, net of tax and refunds")}
                  chart={
                    <div className="vr-form">
                      <BarChart data={rev.data!.revenue_series as never} series={s} label={t("Revenue in {c}", { c: c })} bucket={bucket} format={(n) => money(n, c)} />
                      <BarList label={t("Revenue in {c} by kind", { c: c })} rows={rev.data!.by_kind.filter((k) => k.currency === c).map((k) => ({ name: KIND_WORD[k.kind] ?? k.kind, value: k.net_cents }))} format={(n) => money(n, c)} />
                    </div>
                  }
                  table={seriesTable(rev.data!.revenue_series as never, s, bucket, (n) => money(n, c))}
                />
              );
            })}
          </div>

          {subs.data ? (
            <div className="vr-grid vr-grid-2">
              <ChartCard
                title={t("Subscriptions")}
                subtitle={t("{active} active · {past_due} past due · {new} new and {canceled} ended in this range", { active: fmt(subs.data.now.active), past_due: fmt(subs.data.now.past_due), new: fmt(subs.data.totals.new), canceled: fmt(subs.data.totals.canceled) })}
                chart={<BarChart data={subs.data.series as never} series={subSeries} label={t("New, upgraded and ended subscriptions")} bucket={bucket} />}
                table={seriesTable(subs.data.series as never, subSeries, bucket)}
              />
              <ChartCard
                title={t("Active subscriptions by plan")}
                chart={<BarList label={t("Active subscriptions by plan")} rows={subs.data.by_plan.map((p) => ({ name: `${p.plan} (${p.currency})`, value: p.count }))} />}
                table={{ columns: ["Plan", "Workspaces"], rows: subs.data.by_plan.map((p) => [`${p.plan} (${p.currency})`, fmt(p.count)]) }}
              />
            </div>
          ) : null}

          {costs.data ? (
            <div className="vr-grid vr-grid-2">
              <ChartCard
                title={t("AI provider cost")}
                subtitle={t("{total_usd} at list prices · {total_usd2}", { total_usd: usd(costs.data.total_usd), total_usd2: delta(costs.data.total_usd, costs.data.previous_usd, days) })}
                chart={<BarChart data={costs.data.series} series={[{ key: "cost", label: t("Cost in USD"), tone: "s2" }]} label={t("AI provider cost in USD")} bucket={bucket} format={usd} counts={false} />}
                table={seriesTable(costs.data.series, [{ key: "cost", label: t("Cost in USD"), tone: "s2" }], bucket, usd)}
              />
              <ChartCard
                title={t("Cost by model and feature")}
                chart={
                  <div className="vr-form">
                    <BarList label={t("Cost by model")} rows={costs.data.by_model.map((m) => ({ name: m.name, value: m.cost_usd }))} format={usd} tone="s1" />
                    <BarList label={t("Cost by feature")} rows={costs.data.by_feature.map((m) => ({ name: opLabel(m.name), value: m.cost_usd }))} format={usd} />
                  </div>
                }
                table={{ columns: [t("Model or feature"), t("Cost (USD)")], rows: [...costs.data.by_model, ...costs.data.by_feature.map((f) => ({ ...f, name: opLabel(f.name) }))].map((m) => [m.name, usd(m.cost_usd)]) }}
              />
            </div>
          ) : null}

          {failed.data ? (
            <div className="vr-grid vr-grid-2">
              <ChartCard
                title={t("Failed renewals")}
                subtitle={failed.data.at_risk.length ? t("At risk: {v}", { v: failed.data.at_risk.map((a) => money(a.amount_cents, a.currency)).join(" · ") }) : t("Nothing at risk")}
                chart={<BarChart data={failed.data.series as never} series={failSeries} label={t("Failed and recovered renewals")} bucket={bucket} />}
                table={seriesTable(failed.data.series as never, failSeries, bucket)}
              />
              <Card title={t("Past due now")} padded={false}>
                {failed.data.past_due.length === 0 ? (
                  <EmptyState icon="check" title={t("No workspace is past due")} />
                ) : (
                  <div className="vr-table-wrap">
                    <table className="vr-table">
                      <thead>
                        <tr>
                          <th>{t("Workspace")}</th>
                          <th>{t("Since")}</th>
                          <th>{t("Next retry")}</th>
                          <th className="num">{t("Amount")}</th>
                        </tr>
                      </thead>
                      <tbody>
                        {failed.data.past_due.map((p) => (
                          <tr key={p.workspace_id}>
                            <td>
                              {p.workspace}
                              <div className="vr-muted vr-caption">{p.plan} · {p.retries} {t("retries")}</div>
                            </td>
                            <td>{date(p.since)}</td>
                            <td>{p.next_retry_at ? date(p.next_retry_at) : t("No retries left")}</td>
                            <td className="num">{p.amount_cents !== null && p.currency ? money(p.amount_cents, p.currency) : "—"}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
              </Card>
            </div>
          ) : null}

          {usage.data ? (
            <Card title={t("Workspace usage · {active_workspaces} used AI in this range", { active_workspaces: fmt(usage.data.active_workspaces) })} padded={false}>
              <div className="vr-table-wrap">
                <table className="vr-table">
                  <thead>
                    <tr>
                      <th>{t("Workspace")}</th>
                      <th>{t("Plan")}</th>
                      <th className="num">{t("Weighted tokens")}</th>
                      <th className="num">{t("Provider cost")}</th>
                      <th className="num">{t("Tickets")}</th>
                      <th className="num">{t("Conversations")}</th>
                      <th className="num">{t("Members")}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {usage.data.workspaces.map((w) => (
                      <tr key={w.id}>
                        <td>{w.name}</td>
                        <td>
                          {w.plan ?? t("No plan")}
                          {w.subscription_status === "past_due" ? <span className="vr-word-danger"> {t("· past due")}</span> : null}
                        </td>
                        <td className="num">{fmt(w.weighted_tokens)}</td>
                        <td className="num">{usd(w.provider_cost_usd)}</td>
                        <td className="num">{fmt(w.tickets)}</td>
                        <td className="num">{fmt(w.conversations)}</td>
                        <td className="num">{fmt(w.members)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </Card>
          ) : null}
        </>
      )}
    </div>
  );
}

// ---- Payment provider ---------------------------------------------------------------------------------------

type Events = { provider: string; webhook_url: string | null; events: { id: string; event_id: string; type: string; status: string; error: string | null; workspace: string | null; received_at: string }[] };
const EVENT_STATUS: Record<string, { tone: Tone; word: string }> = {
  processed: { tone: "brand", word: msg("Applied") },
  ignored: { tone: "neutral", word: msg("Not needed") },
  failed: { tone: "danger", word: msg("Failed · will retry") },
  processing: { tone: "warn", word: msg("In progress") },
};

export function PaymentsAdmin() {
  const ev = useData<Events>("/admin/billing/events");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  if (ev.loading && !ev.data) return <Spinner />;
  if (ev.error) return <Banner tone="danger">{ev.error.message}</Banner>;
  const d = ev.data!;
  return (
    <>
      <Card title={t("Payment provider")}>
        <dl className="vr-kv">
          <dt>{t("Provider")}</dt>
          <dd>{d.provider === "stripe" ? t("Stripe (Checkout, Customer Portal, Stripe Tax)") : t("Test provider: nothing is charged")}</dd>
          {d.webhook_url ? (
            <>
              <dt>{t("Webhook endpoint")}</dt>
              <dd className="vr-mono" style={{ fontSize: 13 }}>
                {d.webhook_url}
              </dd>
            </>
          ) : null}
        </dl>
        {d.provider === "mock" ? (
          <p className="vr-muted vr-caption" style={{ marginTop: 12 }}>
            {t("Set VERITY_PAYMENT_PROVIDER=stripe with VERITY_STRIPE_SECRET_KEY and VERITY_STRIPE_WEBHOOK_SECRET to take real payments.")}
          </p>
        ) : null}
      </Card>
      <Card
        title={t("Recent provider events")}
        padded={false}
        actions={
          <Button
            size="sm"
            variant="ghost"
            busy={busy}
            onClick={async () => {
              setBusy(true);
              try {
                await ev.reload();
              } catch (e) {
                setMsg(errorText(e));
              }
              setBusy(false);
            }}
          >
            {t("Refresh")}
          </Button>
        }
      >
        {msg ? <Banner tone="danger">{msg}</Banner> : null}
        {d.events.length === 0 ? (
          <EmptyState icon="clock" title={t("No events yet")} />
        ) : (
          <div className="vr-table-wrap">
            <table className="vr-table">
              <thead>
                <tr>
                  <th>{t("Received")}</th>
                  <th>{t("Event")}</th>
                  <th>{t("Workspace")}</th>
                  <th>{t("Outcome")}</th>
                </tr>
              </thead>
              <tbody>
                {d.events.map((e) => (
                  <tr key={e.id}>
                    <td>{dateTime(e.received_at)}</td>
                    <td>
                      <span className="vr-mono" style={{ fontSize: 12 }}>
                        {e.type}
                      </span>
                      <div className="vr-muted vr-caption vr-mono">{e.event_id}</div>
                    </td>
                    <td>{e.workspace ?? "—"}</td>
                    <td>
                      <Badge tone={(EVENT_STATUS[e.status] ?? EVENT_STATUS.processing).tone} dot>
                        {(EVENT_STATUS[e.status] ?? EVENT_STATUS.processing).word}
                      </Badge>
                      {e.error ? <div className="vr-muted vr-caption">{e.error}</div> : null}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </>
  );
}
