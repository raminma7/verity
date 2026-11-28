"use client";

import { t as tr, msg } from "@/i18n";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";

import { PaymentsAdmin, PlatformReports, TokenPackagesAdmin } from "@/components/admin-billing";
import { Badge, Banner, Button, Card, compact, date, dateTime, EmptyState, Field, fmt, money, PageHeader, Spinner, Stat, Switch } from "@/components/ui";
import { api, errorText, type Limits, type Plan } from "@/lib/api";
import { LIMIT_KEYS, LIMIT_LABELS } from "@/lib/limits";
import { useSession } from "@/lib/session";
import { useData } from "@/lib/use-data";

const TABS = ["overview", "reports", "users", "workspaces", "plans", "packages", "payments", "multipliers", "settings", "audit"] as const;
type Tab = (typeof TABS)[number];
const TAB_LABEL: Record<Tab, string> = { overview: msg("Overview"), reports: msg("Reports"), packages: msg("Token packages"), payments: msg("Payments"), users: msg("Users"), workspaces: msg("Workspaces"), plans: msg("Plans"), multipliers: msg("Model weights"), settings: msg("Defaults"), audit: msg("Audit log") };

function useAction() {
  const [busy, setBusy] = useState<string | null>(null);
  const [msg, setMsg] = useState<{ tone: "brand" | "danger"; text: string } | null>(null);
  async function run<T>(key: string, fn: () => Promise<T>, ok?: string | ((r: T) => string)) {
    setBusy(key);
    setMsg(null);
    try {
      const r = await fn();
      if (ok) setMsg({ tone: "brand", text: typeof ok === "function" ? ok(r) : ok });
    } catch (e) {
      setMsg({ tone: "danger", text: errorText(e) });
    } finally {
      setBusy(null);
    }
  }
  return { busy, msg, run };
}

function Overview() {
  const o = useData<{ users: number; users_suspended: number; workspaces: number; active_subscriptions: number; mrr_cents: number; tokens_this_month: number; provider_cost_this_month_usd: number }>("/admin/overview");
  const report = useData<{ workspace_id: string; workspace: string; input_tokens: number; output_tokens: number; weighted: number; provider_cost_usd: number }[]>("/admin/reports/usage");
  const a = useAction();
  if (!o.data) return <Spinner />;
  const d = o.data;
  return (
    <>
      <div className="vr-grid vr-grid-4">
        <Stat label={tr("Users")} value={fmt(d.users)} note={tr("{users_suspended} suspended", { users_suspended: d.users_suspended })} />
        <Stat label={tr("Workspaces")} value={fmt(d.workspaces)} note={tr("{active_subscriptions} paying", { active_subscriptions: d.active_subscriptions })} />
        <Stat label={tr("MRR")} value={money(d.mrr_cents)} note={tr("Monthly recurring revenue")} />
        <Stat label={tr("AI cost this month")} value={`$${d.provider_cost_this_month_usd.toFixed(2)}`} note={tr("{tokens_this_month} weighted tokens", { tokens_this_month: compact(d.tokens_this_month) })} />
      </div>
      {a.msg ? <Banner tone={a.msg.tone}>{a.msg.text}</Banner> : null}
      <Card title={tr("Jobs")} actions={<span className="vr-help">{tr("The worker also runs these every 5 minutes.")}</span>}>
        <div className="vr-actions">
          <Button icon="refresh" busy={a.busy === "renew"} onClick={() => a.run("renew", () => api.post<{ renewed: number; canceled: number }>("/admin/jobs/renewals"), (r) => `Renewed ${r.renewed}, ended ${r.canceled}.`).then(o.reload)}>
            {tr("Run renewals")}
          </Button>
          <Button icon="clock" busy={a.busy === "stale"} onClick={() => a.run("stale", () => api.post<{ released: number }>("/admin/jobs/release-stale-reservations"), (r) => `Released ${r.released} stale token holds.`)}>
            {tr("Release stale holds")}
          </Button>
        </div>
      </Card>
      <Card title={tr("AI usage and provider cost · last 30 days")} padded={false}>
        {!report.data?.length ? (
          <EmptyState icon="chart" title={tr("No AI usage yet")} />
        ) : (
          <div className="vr-table-wrap">
            <table className="vr-table">
              <thead>
                <tr>
                  <th>{tr("Workspace")}</th>
                  <th className="num">{tr("Input")}</th>
                  <th className="num">{tr("Output")}</th>
                  <th className="num">{tr("Weighted")}</th>
                  <th className="num">{tr("Provider cost")}</th>
                </tr>
              </thead>
              <tbody>
                {report.data.map((r) => (
                  <tr key={r.workspace_id}>
                    <td>{r.workspace}</td>
                    <td className="num">{fmt(r.input_tokens)}</td>
                    <td className="num">{fmt(r.output_tokens)}</td>
                    <td className="num">{fmt(r.weighted)}</td>
                    <td className="num">${r.provider_cost_usd.toFixed(4)}</td>
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

type AdminUser = { id: string; full_name: string; email: string; status: string; email_verified: boolean; is_system_admin: boolean; totp_enabled: boolean; workspaces: number; created_at: string; last_login_at: string | null };

function Users() {
  const { user: me } = useSession();
  const [q, setQ] = useState("");
  const [query, setQuery] = useState("");
  const users = useData<AdminUser[]>(`/admin/users?q=${encodeURIComponent(query)}`);
  const a = useAction();
  return (
    <>
      <form className="vr-actions" onSubmit={(e) => { e.preventDefault(); setQuery(q); }}>
        <div style={{ flex: 1, maxWidth: 420 }}>
          <Field icon="search" placeholder={tr("Search name or email")} value={q} onChange={(e) => setQ(e.target.value)} aria-label={tr("Search users")} />
        </div>
        <Button type="submit">{tr("Search")}</Button>
      </form>
      {a.msg ? <Banner tone={a.msg.tone}>{a.msg.text}</Banner> : null}
      <Card padded={false}>
        <div className="vr-table-wrap">
          <table className="vr-table">
            <thead>
              <tr>
                <th>{tr("User")}</th>
                <th>{tr("Status")}</th>
                <th className="num">{tr("Workspaces")}</th>
                <th>{tr("Joined")}</th>
                <th>{tr("Last sign-in")}</th>
                <th><span className="sr-only">{tr("Actions")}</span></th>
              </tr>
            </thead>
            <tbody>
              {(users.data ?? []).map((u) => (
                <tr key={u.id}>
                  <td>
                    <span className="vr-person">
                      <span>
                        <b>
                          {u.full_name} {u.is_system_admin ? <Badge tone="brand">{tr("Admin")}</Badge> : null}
                        </b>
                        <small>{u.email}</small>
                      </span>
                    </span>
                  </td>
                  <td>
                    <span className="vr-actions">
                      {u.status === "suspended" ? <Badge tone="danger" dot>{tr("Suspended")}</Badge> : u.email_verified ? <Badge tone="brand" dot>{tr("Active")}</Badge> : <Badge tone="warn" dot>{tr("Unverified")}</Badge>}
                      {u.totp_enabled ? <Badge>{tr("2FA")}</Badge> : null}
                    </span>
                  </td>
                  <td className="num">{u.workspaces}</td>
                  <td className="vr-muted">{date(u.created_at)}</td>
                  <td className="vr-muted">{dateTime(u.last_login_at)}</td>
                  <td style={{ textAlign: "right" }}>
                    {u.id !== me.id ? (
                      <Button
                        variant={u.status === "suspended" ? "secondary" : "danger"}
                        size="sm"
                        busy={a.busy === u.id}
                        onClick={() => {
                          const next = u.status === "suspended" ? "active" : "suspended";
                          const reason = next === "suspended" ? prompt(`Why suspend ${u.email}? (kept in the audit log)`) : "";
                          if (reason === null) return;
                          a.run(u.id, async () => { await api.patch(`/admin/users/${u.id}/status`, { status: next, reason }); await users.reload(); }, next === "suspended" ? `${u.email} suspended and signed out.` : `${u.email} reactivated.`);
                        }}
                      >
                        {u.status === "suspended" ? tr("Reactivate") : tr("Suspend")}
                      </Button>
                    ) : null}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>
    </>
  );
}

type AdminWs = { id: string; name: string; slug: string; status: string; owner: { email: string; full_name: string }; members: number; plan: string | null; subscription_status: string | null; renews_at: string | null; tokens: null | { allowance: number; bonus: number; consumed: number; reserved: number }; created_at: string };

function Workspaces() {
  const [q, setQ] = useState("");
  const [query, setQuery] = useState("");
  const list = useData<AdminWs[]>(`/admin/workspaces?q=${encodeURIComponent(query)}`);
  const a = useAction();
  return (
    <>
      <form className="vr-actions" onSubmit={(e) => { e.preventDefault(); setQuery(q); }}>
        <div style={{ flex: 1, maxWidth: 420 }}>
          <Field icon="search" placeholder={tr("Search workspace or owner email")} value={q} onChange={(e) => setQ(e.target.value)} aria-label={tr("Search workspaces")} />
        </div>
        <Button type="submit">{tr("Search")}</Button>
      </form>
      {a.msg ? <Banner tone={a.msg.tone}>{a.msg.text}</Banner> : null}
      <Card padded={false}>
        <div className="vr-table-wrap">
          <table className="vr-table">
            <thead>
              <tr>
                <th>{tr("Workspace")}</th>
                <th>{tr("Plan")}</th>
                <th className="num">{tr("Members")}</th>
                <th className="num">{tr("Tokens used")}</th>
                <th>{tr("Status")}</th>
                <th><span className="sr-only">{tr("Actions")}</span></th>
              </tr>
            </thead>
            <tbody>
              {(list.data ?? []).map((w) => (
                <tr key={w.id}>
                  <td>
                    <span className="vr-person">
                      <span>
                        <b>{w.name}</b>
                        <small>{w.owner.email}</small>
                      </span>
                    </span>
                  </td>
                  <td>{w.plan ? `${w.plan}${w.subscription_status !== "active" ? ` (${w.subscription_status})` : ""}` : <span className="vr-muted">{tr("None")}</span>}</td>
                  <td className="num">{w.members}</td>
                  <td className="num">{w.tokens ? `${compact(w.tokens.consumed)} / ${compact(w.tokens.allowance + w.tokens.bonus)}` : "—"}</td>
                  <td>{w.status === "active" ? <Badge tone="brand" dot>{tr("Active")}</Badge> : <Badge tone="danger" dot>{tr("Suspended")}</Badge>}</td>
                  <td style={{ textAlign: "right", whiteSpace: "nowrap" }}>
                    {w.tokens ? (
                      <Button
                        variant="ghost"
                        size="sm"
                        onClick={() => {
                          const raw = prompt(`Add (or remove, with a minus) weighted tokens for ${w.name}:`, "100000");
                          if (!raw) return;
                          const reason = prompt("Reason (kept in the audit log):", "Goodwill credit");
                          if (!reason) return;
                          a.run(`t-${w.id}`, async () => { await api.post(`/admin/workspaces/${w.id}/tokens`, { delta: parseInt(raw, 10), reason }); await list.reload(); }, tr("Adjusted {name} by {fmt} tokens.", { name: w.name, fmt: fmt(parseInt(raw, 10)) }));
                        }}
                      >
                        {tr("Adjust tokens")}
                      </Button>
                    ) : null}
                    <Button
                      variant={w.status === "active" ? "danger" : "secondary"}
                      size="sm"
                      busy={a.busy === w.id}
                      onClick={() => {
                        const next = w.status === "active" ? "suspended" : "active";
                        const reason = next === "suspended" ? prompt(`Why suspend ${w.name}?`) : "";
                        if (reason === null) return;
                        a.run(w.id, async () => { await api.patch(`/admin/workspaces/${w.id}/status`, { status: next, reason }); await list.reload(); });
                      }}
                    >
                      {w.status === "active" ? tr("Suspend") : tr("Reactivate")}
                    </Button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>
    </>
  );
}

const EMPTY_LIMITS = Object.fromEntries(LIMIT_KEYS.map((k) => [k, 0])) as Limits;

function PlanEditor({ plan, onDone }: { plan: Plan | null; onDone: () => void }) {
  const [p, setP] = useState({
    key: plan?.key ?? "",
    name: plan?.name ?? "",
    description: plan?.description ?? "",
    price: plan ? String(plan.price_cents / 100) : "",
    currency: plan?.currency ?? "USD",
    billing_cycle: plan?.billing_cycle ?? "month",
    token_allowance: String(plan?.token_allowance ?? 1000000),
    limits: { ...EMPTY_LIMITS, ...(plan?.limits ?? {}) } as Limits,
    is_public: plan?.is_public ?? true,
    is_active: plan?.is_active ?? true,
    sort_order: String(plan?.sort_order ?? 0),
  });
  const a = useAction();
  const body = {
    name: p.name,
    description: p.description,
    price_cents: Math.round(parseFloat(p.price || "0") * 100),
    currency: p.currency.toUpperCase(),
    billing_cycle: p.billing_cycle,
    token_allowance: parseInt(p.token_allowance || "0", 10),
    limits: Object.fromEntries(Object.entries(p.limits).map(([k, v]) => [k, Number(v)])),
    is_public: p.is_public,
    is_active: p.is_active,
    sort_order: parseInt(p.sort_order || "0", 10),
  };
  return (
    <Card title={plan ? tr("Edit {name}", { name: plan.name }) : tr("New plan")}>
      <form
        className="vr-form"
        onSubmit={(e) => {
          e.preventDefault();
          a.run("save", async () => {
            if (plan) await api.patch(`/admin/plans/${plan.id}`, body);
            else await api.post("/admin/plans", { ...body, key: p.key });
            onDone();
          });
        }}
      >
        {a.msg ? <Banner tone={a.msg.tone}>{a.msg.text}</Banner> : null}
        {plan ? <Banner tone="neutral">{tr("Changes apply to new purchases now and to existing subscribers at their next renewal.")}</Banner> : null}
        <div className="vr-grid vr-grid-3">
          {!plan ? <Field label={tr("Key")} value={p.key} onChange={(e) => setP({ ...p, key: e.target.value })} help={tr("Lowercase, e.g. enterprise")} /> : null}
          <Field label={tr("Name")} value={p.name} onChange={(e) => setP({ ...p, name: e.target.value })} />
          <Field label={tr("Price ({currency})", { currency: p.currency })} inputMode="decimal" value={p.price} onChange={(e) => setP({ ...p, price: e.target.value })} />
          <Field label={tr("Currency")} maxLength={3} value={p.currency} onChange={(e) => setP({ ...p, currency: e.target.value })} />
          <Field label={tr("Billing cycle (month / year)")} value={p.billing_cycle} onChange={(e) => setP({ ...p, billing_cycle: e.target.value as "month" | "year" })} />
          <Field label={tr("AI tokens per cycle")} inputMode="numeric" value={p.token_allowance} onChange={(e) => setP({ ...p, token_allowance: e.target.value })} />
          <Field label={tr("Sort order")} inputMode="numeric" value={p.sort_order} onChange={(e) => setP({ ...p, sort_order: e.target.value })} />
        </div>
        <Field label={tr("Description")} value={p.description} onChange={(e) => setP({ ...p, description: e.target.value })} />
        <span className="vr-eyebrow">{tr("Limits · −1 means unlimited")}</span>
        <div className="vr-grid vr-grid-4">
          {LIMIT_KEYS.map((k) => (
            <Field key={k} label={LIMIT_LABELS[k] + (k === "storage_mb" ? " (MB)" : "")} inputMode="numeric" value={String(p.limits[k])} onChange={(e) => setP({ ...p, limits: { ...p.limits, [k]: e.target.value as unknown as number } })} />
          ))}
        </div>
        <div className="vr-grid vr-grid-2">
          <Switch label={tr("Public")} help={tr("Shown on the plans page.")} checked={p.is_public} onChange={(v) => setP({ ...p, is_public: v })} />
          <Switch label={tr("Available")} help={tr("Off stops new purchases and renewals.")} checked={p.is_active} onChange={(v) => setP({ ...p, is_active: v })} />
        </div>
        <div className="vr-actions">
          <Button type="submit" variant="primary" busy={a.busy === "save"}>
            {plan ? tr("Save plan") : tr("Create plan")}
          </Button>
          <Button variant="ghost" onClick={onDone}>
            {tr("Cancel")}
          </Button>
        </div>
      </form>
    </Card>
  );
}

function Plans() {
  const plans = useData<Plan[]>("/admin/plans");
  const [editing, setEditing] = useState<Plan | "new" | null>(null);
  if (editing)
    return (
      <PlanEditor
        plan={editing === "new" ? null : editing}
        onDone={() => {
          setEditing(null);
          plans.reload();
        }}
      />
    );
  return (
    <Card title={tr("Subscription plans")} actions={<Button size="sm" variant="primary" icon="plus" onClick={() => setEditing("new")}>{tr("New plan")}</Button>} padded={false}>
      <div className="vr-table-wrap">
        <table className="vr-table">
          <thead>
            <tr>
              <th>{tr("Plan")}</th>
              <th className="num">{tr("Price")}</th>
              <th className="num">{tr("Tokens")}</th>
              <th className="num">{tr("Members")}</th>
              <th className="num">{tr("Documents")}</th>
              <th>{tr("Visibility")}</th>
              <th><span className="sr-only">{tr("Actions")}</span></th>
            </tr>
          </thead>
          <tbody>
            {(plans.data ?? []).map((p) => (
              <tr key={p.id}>
                <td>
                  <b style={{ fontWeight: 500 }}>{p.name}</b> <span className="vr-mono vr-muted" style={{ fontSize: 12 }}>{p.key}</span>
                </td>
                <td className="num">
                  {money(p.price_cents, p.currency)}/{p.billing_cycle === "year" ? "yr" : "mo"}
                </td>
                <td className="num">{compact(p.token_allowance)}</td>
                <td className="num">{p.limits.members < 0 ? "∞" : p.limits.members}</td>
                <td className="num">{p.limits.documents < 0 ? "∞" : fmt(p.limits.documents)}</td>
                <td>
                  {!p.is_active ? <Badge tone="danger">{tr("Retired")}</Badge> : p.is_public ? <Badge tone="brand">{tr("Public")}</Badge> : <Badge>{tr("Hidden")}</Badge>}
                </td>
                <td style={{ textAlign: "right" }}>
                  <Button size="sm" variant="ghost" onClick={() => setEditing(p)}>
                    {tr("Edit")}
                  </Button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Card>
  );
}

type Mult = { id: string; model: string; operation: string; input_multiplier: number; output_multiplier: number; input_cost_per_m: number; output_cost_per_m: number };

function Multipliers() {
  const list = useData<Mult[]>("/admin/multipliers");
  const [f, setF] = useState({ model: "", operation: "*", input_multiplier: "1", output_multiplier: "1", input_cost_per_m: "0", output_cost_per_m: "0" });
  const a = useAction();
  return (
    <>
      {a.msg ? <Banner tone={a.msg.tone}>{a.msg.text}</Banner> : null}
      <Card title={tr("Model weights")} padded={false}>
        <div className="vr-panel-body" style={{ paddingBottom: 0 }}>
          <p className="vr-muted" style={{ fontSize: 13 }}>
            {tr("Weighted tokens = input × input weight + output × output weight. Customers see these weights on their usage page. Provider prices feed the cost report.")}
          </p>
        </div>
        <div className="vr-table-wrap" style={{ marginTop: 12 }}>
          <table className="vr-table">
            <thead>
              <tr>
                <th>{tr("Model")}</th>
                <th>{tr("Operation")}</th>
                <th className="num">{tr("Input ×")}</th>
                <th className="num">{tr("Output ×")}</th>
                <th className="num">{tr("$ / 1M in")}</th>
                <th className="num">{tr("$ / 1M out")}</th>
                <th><span className="sr-only">{tr("Actions")}</span></th>
              </tr>
            </thead>
            <tbody>
              {(list.data ?? []).map((m) => (
                <tr key={m.id}>
                  <td className="vr-mono" style={{ fontSize: 13 }}>
                    {m.model}
                  </td>
                  <td className="vr-mono" style={{ fontSize: 13 }}>
                    {m.operation}
                  </td>
                  <td className="num">{m.input_multiplier}</td>
                  <td className="num">{m.output_multiplier}</td>
                  <td className="num">{m.input_cost_per_m}</td>
                  <td className="num">{m.output_cost_per_m}</td>
                  <td style={{ textAlign: "right" }}>
                    <Button size="sm" variant="ghost" onClick={() => setF({ model: m.model, operation: m.operation, input_multiplier: String(m.input_multiplier), output_multiplier: String(m.output_multiplier), input_cost_per_m: String(m.input_cost_per_m), output_cost_per_m: String(m.output_cost_per_m) })}>
                      {tr("Edit")}
                    </Button>
                    <Button size="sm" variant="ghost" onClick={() => a.run(m.id, async () => { await api.del(`/admin/multipliers/${m.id}`); await list.reload(); })}>
                      {tr("Delete")}
                    </Button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>
      <Card title={tr("Add or update a weight")}>
        <form
          className="vr-form"
          onSubmit={(e) => {
            e.preventDefault();
            a.run("save", async () => {
              await api.put("/admin/multipliers", {
                model: f.model,
                operation: f.operation || "*",
                input_multiplier: Number(f.input_multiplier),
                output_multiplier: Number(f.output_multiplier),
                input_cost_per_m: Number(f.input_cost_per_m),
                output_cost_per_m: Number(f.output_cost_per_m),
              });
              await list.reload();
            }, tr("Weight saved."));
          }}
        >
          <div className="vr-grid vr-grid-3">
            <Field label={tr("Model")} className="vr-mono" value={f.model} onChange={(e) => setF({ ...f, model: e.target.value })} placeholder="claude-sonnet-5-5" />
            <Field label={tr("Operation")} value={f.operation} onChange={(e) => setF({ ...f, operation: e.target.value })} help={tr("* for all, or chat_response, kb_ask, tool_call, form_activation, form_prefill, classification, auto_reply, embedding")} />
            <Field label={tr("Input weight")} inputMode="decimal" value={f.input_multiplier} onChange={(e) => setF({ ...f, input_multiplier: e.target.value })} />
            <Field label={tr("Output weight")} inputMode="decimal" value={f.output_multiplier} onChange={(e) => setF({ ...f, output_multiplier: e.target.value })} />
            <Field label={tr("Provider $ / 1M input")} inputMode="decimal" value={f.input_cost_per_m} onChange={(e) => setF({ ...f, input_cost_per_m: e.target.value })} />
            <Field label={tr("Provider $ / 1M output")} inputMode="decimal" value={f.output_cost_per_m} onChange={(e) => setF({ ...f, output_cost_per_m: e.target.value })} />
          </div>
          <div>
            <Button type="submit" variant="primary" busy={a.busy === "save"} disabled={!f.model}>
              {tr("Save weight")}
            </Button>
          </div>
        </form>
      </Card>
    </>
  );
}

const RETENTION_LABELS: Record<string, string> = {
  closed_tickets: msg("Closed tickets"),
  resolved_conversations: msg("Resolved conversations"),
  email_content: msg("Email bodies and attachments"),
  spam: msg("Mail held in Spam"),
};

function Defaults() {
  const s = useData<{ no_plan_limits: Limits; audit_retention_days: number; retention_max: Record<string, number | null> }>("/admin/settings");
  const [caps, setCaps] = useState<Record<string, string> | null>(null);
  const [limits, setLimits] = useState<Limits | null>(null);
  const [days, setDays] = useState<string | null>(null);
  const a = useAction();
  if (!s.data) return <Spinner />;
  const L = limits ?? s.data.no_plan_limits;
  return (
    <Card title={tr("Workspaces without a plan")}>
      <form
        className="vr-form"
        onSubmit={(e) => {
          e.preventDefault();
          a.run("save", async () => {
            await api.put("/admin/settings", {
              no_plan_limits: Object.fromEntries(Object.entries(L).map(([k, v]) => [k, Number(v)])),
              audit_retention_days: Number(days ?? s.data!.audit_retention_days),
              ...(caps ? { retention_max: Object.fromEntries(Object.entries(caps).map(([k, v]) => [k, v.trim() ? Number(v) : null])) } : {}),
            });
            setCaps(null);
            await s.reload();
          }, tr("Defaults saved."));
        }}
      >
        {a.msg ? <Banner tone={a.msg.tone}>{a.msg.text}</Banner> : null}
        <p className="vr-muted" style={{ fontSize: 13 }}>
          {tr("Limits for workspaces that haven’t bought a plan or whose plan ended. Paid plans set their own limits.")}
        </p>
        <div className="vr-grid vr-grid-4">
          {LIMIT_KEYS.map((k) => (
            <Field key={k} label={tr(LIMIT_LABELS[k])} inputMode="numeric" value={String(L[k])} onChange={(e) => setLimits({ ...L, [k]: e.target.value as unknown as number })} />
          ))}
        </div>
        <hr className="vr-sep" />
        <div className="vr-form">
          <b>{tr("Maximum data retention (days)")}</b>
          <p className="vr-help" style={{ margin: 0 }}>
            {tr("No workspace keeps these longer than this, whatever it sets. Leave a field empty for no maximum.")}
          </p>
          <div className="vr-grid vr-grid-4">
            {Object.entries(RETENTION_LABELS).map(([k, label]) => (
              <Field
                key={k}
                label={tr(label)}
                inputMode="numeric"
                value={caps?.[k] ?? (s.data!.retention_max[k] === null || s.data!.retention_max[k] === undefined ? "" : String(s.data!.retention_max[k]))}
                onChange={(e) =>
                  setCaps({
                    ...Object.fromEntries(Object.entries(s.data!.retention_max).map(([kk, vv]) => [kk, vv === null ? "" : String(vv)])),
                    ...(caps ?? {}),
                    [k]: e.target.value,
                  })
                }
              />
            ))}
          </div>
        </div>
        <div style={{ maxWidth: 260 }}>
          <Field label={tr("Keep audit logs for (days)")} inputMode="numeric" value={days ?? String(s.data.audit_retention_days)} onChange={(e) => setDays(e.target.value)} help={tr("At least 30. Older entries are deleted by the worker.")} />
        </div>
        <div>
          <Button type="submit" variant="primary" busy={a.busy === "save"}>
            {tr("Save defaults")}
          </Button>
        </div>
      </form>
    </Card>
  );
}

type Log = { id: string; action: string; actor: string | null; workspace_id: string | null; target_type: string | null; target_id: string | null; data: Record<string, unknown>; ip: string | null; created_at: string };

function Audit() {
  const [action, setAction] = useState("");
  const [query, setQuery] = useState("");
  const logs = useData<Log[]>(`/admin/audit-logs?action=${encodeURIComponent(query)}`);
  return (
    <>
      <form className="vr-actions" onSubmit={(e) => { e.preventDefault(); setQuery(action); }}>
        <div style={{ flex: 1, maxWidth: 420 }}>
          <Field icon="search" placeholder={tr("Filter by action prefix, e.g. admin. or user.login")} value={action} onChange={(e) => setAction(e.target.value)} aria-label={tr("Filter audit log")} />
        </div>
        <Button type="submit">{tr("Filter")}</Button>
      </form>
      <Card padded={false}>
        <div className="vr-table-wrap">
          <table className="vr-table">
            <thead>
              <tr>
                <th>{tr("When")}</th>
                <th>{tr("Action")}</th>
                <th>{tr("Actor")}</th>
                <th>{tr("Target")}</th>
                <th>{tr("Details")}</th>
              </tr>
            </thead>
            <tbody>
              {(logs.data ?? []).map((l) => (
                <tr key={l.id}>
                  <td className="vr-muted" style={{ whiteSpace: "nowrap" }}>
                    {dateTime(l.created_at)}
                  </td>
                  <td className="vr-mono" style={{ fontSize: 13 }}>
                    {l.action}
                  </td>
                  <td>{l.actor ?? <span className="vr-muted">{tr("System")}</span>}</td>
                  <td className="vr-mono vr-muted" style={{ fontSize: 12 }}>
                    {l.target_type ? `${l.target_type}:${(l.target_id ?? "").slice(0, 18)}` : "—"}
                  </td>
                  <td className="vr-mono vr-muted" style={{ fontSize: 12, maxWidth: 320, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }} title={JSON.stringify(l.data)}>
                    {Object.keys(l.data).length ? JSON.stringify(l.data) : ""}
                    {l.ip ? ` ${l.ip}` : ""}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>
    </>
  );
}

function Admin() {
  const { user } = useSession();
  const router = useRouter();
  const tab = (useSearchParams().get("tab") as Tab) || "overview";
  if (!user.is_system_admin) return <Banner tone="danger">{tr("This area is for system administrators.")}</Banner>;
  return (
    <>
      <PageHeader eyebrow={tr("System admin")} title={tr("Platform administration")} subtitle={tr("Plans, global limits, AI weights and accounts across every workspace. Every change here is audit-logged.")} />
      <nav className="vr-tabs" aria-label={tr("Admin sections")}>
        {TABS.map((t) => (
          <button key={t} type="button" className={t === tab ? "is-on" : undefined} aria-current={t === tab ? "page" : undefined} onClick={() => router.replace(`/admin?tab=${t}`)}>
            {tr(TAB_LABEL[t])}
          </button>
        ))}
      </nav>
      {tab === "overview" ? <Overview /> : tab === "reports" ? <PlatformReports /> : tab === "packages" ? <TokenPackagesAdmin /> : tab === "payments" ? <PaymentsAdmin /> : tab === "users" ? <Users /> : tab === "workspaces" ? <Workspaces /> : tab === "plans" ? <Plans /> : tab === "multipliers" ? <Multipliers /> : tab === "settings" ? <Defaults /> : <Audit />}
    </>
  );
}

export default function Page() {
  return (
    <Suspense>
      <Admin />
    </Suspense>
  );
}
