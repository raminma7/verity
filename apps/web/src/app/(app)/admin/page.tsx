"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";

import { Badge, Banner, Button, Card, compact, date, dateTime, EmptyState, Field, fmt, money, PageHeader, Spinner, Stat, Switch } from "@/components/ui";
import { api, errorText, type Limits, type Plan } from "@/lib/api";
import { LIMIT_KEYS, LIMIT_LABELS } from "@/lib/limits";
import { useSession } from "@/lib/session";
import { useData } from "@/lib/use-data";

const TABS = ["overview", "users", "workspaces", "plans", "multipliers", "settings", "audit"] as const;
type Tab = (typeof TABS)[number];
const TAB_LABEL: Record<Tab, string> = { overview: "Overview", users: "Users", workspaces: "Workspaces", plans: "Plans", multipliers: "Model weights", settings: "Defaults", audit: "Audit log" };

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
        <Stat label="Users" value={fmt(d.users)} note={`${d.users_suspended} suspended`} />
        <Stat label="Workspaces" value={fmt(d.workspaces)} note={`${d.active_subscriptions} paying`} />
        <Stat label="MRR" value={money(d.mrr_cents)} note="Monthly recurring revenue" />
        <Stat label="AI cost this month" value={`$${d.provider_cost_this_month_usd.toFixed(2)}`} note={`${compact(d.tokens_this_month)} weighted tokens`} />
      </div>
      {a.msg ? <Banner tone={a.msg.tone}>{a.msg.text}</Banner> : null}
      <Card title="Jobs" actions={<span className="vr-help">The worker also runs these every 5 minutes.</span>}>
        <div className="vr-actions">
          <Button icon="refresh" busy={a.busy === "renew"} onClick={() => a.run("renew", () => api.post<{ renewed: number; canceled: number }>("/admin/jobs/renewals"), (r) => `Renewed ${r.renewed}, ended ${r.canceled}.`).then(o.reload)}>
            Run renewals
          </Button>
          <Button icon="clock" busy={a.busy === "stale"} onClick={() => a.run("stale", () => api.post<{ released: number }>("/admin/jobs/release-stale-reservations"), (r) => `Released ${r.released} stale token holds.`)}>
            Release stale holds
          </Button>
        </div>
      </Card>
      <Card title="AI usage and provider cost · last 30 days" padded={false}>
        {!report.data?.length ? (
          <EmptyState icon="chart" title="No AI usage yet" />
        ) : (
          <div className="vr-table-wrap">
            <table className="vr-table">
              <thead>
                <tr>
                  <th>Workspace</th>
                  <th className="num">Input</th>
                  <th className="num">Output</th>
                  <th className="num">Weighted</th>
                  <th className="num">Provider cost</th>
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
          <Field icon="search" placeholder="Search name or email" value={q} onChange={(e) => setQ(e.target.value)} aria-label="Search users" />
        </div>
        <Button type="submit">Search</Button>
      </form>
      {a.msg ? <Banner tone={a.msg.tone}>{a.msg.text}</Banner> : null}
      <Card padded={false}>
        <div className="vr-table-wrap">
          <table className="vr-table">
            <thead>
              <tr>
                <th>User</th>
                <th>Status</th>
                <th className="num">Workspaces</th>
                <th>Joined</th>
                <th>Last sign-in</th>
                <th><span className="sr-only">Actions</span></th>
              </tr>
            </thead>
            <tbody>
              {(users.data ?? []).map((u) => (
                <tr key={u.id}>
                  <td>
                    <span className="vr-person">
                      <span>
                        <b>
                          {u.full_name} {u.is_system_admin ? <Badge tone="brand">Admin</Badge> : null}
                        </b>
                        <small>{u.email}</small>
                      </span>
                    </span>
                  </td>
                  <td>
                    <span className="vr-actions">
                      {u.status === "suspended" ? <Badge tone="danger" dot>Suspended</Badge> : u.email_verified ? <Badge tone="brand" dot>Active</Badge> : <Badge tone="warn" dot>Unverified</Badge>}
                      {u.totp_enabled ? <Badge>2FA</Badge> : null}
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
                        {u.status === "suspended" ? "Reactivate" : "Suspend"}
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
          <Field icon="search" placeholder="Search workspace or owner email" value={q} onChange={(e) => setQ(e.target.value)} aria-label="Search workspaces" />
        </div>
        <Button type="submit">Search</Button>
      </form>
      {a.msg ? <Banner tone={a.msg.tone}>{a.msg.text}</Banner> : null}
      <Card padded={false}>
        <div className="vr-table-wrap">
          <table className="vr-table">
            <thead>
              <tr>
                <th>Workspace</th>
                <th>Plan</th>
                <th className="num">Members</th>
                <th className="num">Tokens used</th>
                <th>Status</th>
                <th><span className="sr-only">Actions</span></th>
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
                  <td>{w.plan ? `${w.plan}${w.subscription_status !== "active" ? ` (${w.subscription_status})` : ""}` : <span className="vr-muted">None</span>}</td>
                  <td className="num">{w.members}</td>
                  <td className="num">{w.tokens ? `${compact(w.tokens.consumed)} / ${compact(w.tokens.allowance + w.tokens.bonus)}` : "—"}</td>
                  <td>{w.status === "active" ? <Badge tone="brand" dot>Active</Badge> : <Badge tone="danger" dot>Suspended</Badge>}</td>
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
                          a.run(`t-${w.id}`, async () => { await api.post(`/admin/workspaces/${w.id}/tokens`, { delta: parseInt(raw, 10), reason }); await list.reload(); }, `Adjusted ${w.name} by ${fmt(parseInt(raw, 10))} tokens.`);
                        }}
                      >
                        Adjust tokens
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
                      {w.status === "active" ? "Suspend" : "Reactivate"}
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
    <Card title={plan ? `Edit ${plan.name}` : "New plan"}>
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
        {plan ? <Banner tone="neutral">Changes apply to new purchases now and to existing subscribers at their next renewal.</Banner> : null}
        <div className="vr-grid vr-grid-3">
          {!plan ? <Field label="Key" value={p.key} onChange={(e) => setP({ ...p, key: e.target.value })} help="Lowercase, e.g. enterprise" /> : null}
          <Field label="Name" value={p.name} onChange={(e) => setP({ ...p, name: e.target.value })} />
          <Field label={`Price (${p.currency})`} inputMode="decimal" value={p.price} onChange={(e) => setP({ ...p, price: e.target.value })} />
          <Field label="Currency" maxLength={3} value={p.currency} onChange={(e) => setP({ ...p, currency: e.target.value })} />
          <Field label="Billing cycle (month / year)" value={p.billing_cycle} onChange={(e) => setP({ ...p, billing_cycle: e.target.value as "month" | "year" })} />
          <Field label="AI tokens per cycle" inputMode="numeric" value={p.token_allowance} onChange={(e) => setP({ ...p, token_allowance: e.target.value })} />
          <Field label="Sort order" inputMode="numeric" value={p.sort_order} onChange={(e) => setP({ ...p, sort_order: e.target.value })} />
        </div>
        <Field label="Description" value={p.description} onChange={(e) => setP({ ...p, description: e.target.value })} />
        <span className="vr-eyebrow">Limits · −1 means unlimited</span>
        <div className="vr-grid vr-grid-4">
          {LIMIT_KEYS.map((k) => (
            <Field key={k} label={LIMIT_LABELS[k] + (k === "storage_mb" ? " (MB)" : "")} inputMode="numeric" value={String(p.limits[k])} onChange={(e) => setP({ ...p, limits: { ...p.limits, [k]: e.target.value as unknown as number } })} />
          ))}
        </div>
        <div className="vr-grid vr-grid-2">
          <Switch label="Public" help="Shown on the plans page." checked={p.is_public} onChange={(v) => setP({ ...p, is_public: v })} />
          <Switch label="Available" help="Off stops new purchases and renewals." checked={p.is_active} onChange={(v) => setP({ ...p, is_active: v })} />
        </div>
        <div className="vr-actions">
          <Button type="submit" variant="primary" busy={a.busy === "save"}>
            {plan ? "Save plan" : "Create plan"}
          </Button>
          <Button variant="ghost" onClick={onDone}>
            Cancel
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
    <Card title="Subscription plans" actions={<Button size="sm" variant="primary" icon="plus" onClick={() => setEditing("new")}>New plan</Button>} padded={false}>
      <div className="vr-table-wrap">
        <table className="vr-table">
          <thead>
            <tr>
              <th>Plan</th>
              <th className="num">Price</th>
              <th className="num">Tokens</th>
              <th className="num">Members</th>
              <th className="num">Documents</th>
              <th>Visibility</th>
              <th><span className="sr-only">Actions</span></th>
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
                  {!p.is_active ? <Badge tone="danger">Retired</Badge> : p.is_public ? <Badge tone="brand">Public</Badge> : <Badge>Hidden</Badge>}
                </td>
                <td style={{ textAlign: "right" }}>
                  <Button size="sm" variant="ghost" onClick={() => setEditing(p)}>
                    Edit
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
      <Card title="Model weights" padded={false}>
        <div className="vr-panel-body" style={{ paddingBottom: 0 }}>
          <p className="vr-muted" style={{ fontSize: 13 }}>
            Weighted tokens = input × input weight + output × output weight. Customers see these weights on their usage page. Provider prices feed the cost report.
          </p>
        </div>
        <div className="vr-table-wrap" style={{ marginTop: 12 }}>
          <table className="vr-table">
            <thead>
              <tr>
                <th>Model</th>
                <th>Operation</th>
                <th className="num">Input ×</th>
                <th className="num">Output ×</th>
                <th className="num">$ / 1M in</th>
                <th className="num">$ / 1M out</th>
                <th><span className="sr-only">Actions</span></th>
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
                      Edit
                    </Button>
                    <Button size="sm" variant="ghost" onClick={() => a.run(m.id, async () => { await api.del(`/admin/multipliers/${m.id}`); await list.reload(); })}>
                      Delete
                    </Button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>
      <Card title="Add or update a weight">
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
            }, "Weight saved.");
          }}
        >
          <div className="vr-grid vr-grid-3">
            <Field label="Model" className="vr-mono" value={f.model} onChange={(e) => setF({ ...f, model: e.target.value })} placeholder="claude-sonnet-5-5" />
            <Field label="Operation" value={f.operation} onChange={(e) => setF({ ...f, operation: e.target.value })} help="* for all, or chat_response, classification, summary, auto_reply, embedding" />
            <Field label="Input weight" inputMode="decimal" value={f.input_multiplier} onChange={(e) => setF({ ...f, input_multiplier: e.target.value })} />
            <Field label="Output weight" inputMode="decimal" value={f.output_multiplier} onChange={(e) => setF({ ...f, output_multiplier: e.target.value })} />
            <Field label="Provider $ / 1M input" inputMode="decimal" value={f.input_cost_per_m} onChange={(e) => setF({ ...f, input_cost_per_m: e.target.value })} />
            <Field label="Provider $ / 1M output" inputMode="decimal" value={f.output_cost_per_m} onChange={(e) => setF({ ...f, output_cost_per_m: e.target.value })} />
          </div>
          <div>
            <Button type="submit" variant="primary" busy={a.busy === "save"} disabled={!f.model}>
              Save weight
            </Button>
          </div>
        </form>
      </Card>
    </>
  );
}

function Defaults() {
  const s = useData<{ no_plan_limits: Limits; audit_retention_days: number }>("/admin/settings");
  const [limits, setLimits] = useState<Limits | null>(null);
  const [days, setDays] = useState<string | null>(null);
  const a = useAction();
  if (!s.data) return <Spinner />;
  const L = limits ?? s.data.no_plan_limits;
  return (
    <Card title="Workspaces without a plan">
      <form
        className="vr-form"
        onSubmit={(e) => {
          e.preventDefault();
          a.run("save", async () => {
            await api.put("/admin/settings", {
              no_plan_limits: Object.fromEntries(Object.entries(L).map(([k, v]) => [k, Number(v)])),
              audit_retention_days: Number(days ?? s.data!.audit_retention_days),
            });
            await s.reload();
          }, "Defaults saved.");
        }}
      >
        {a.msg ? <Banner tone={a.msg.tone}>{a.msg.text}</Banner> : null}
        <p className="vr-muted" style={{ fontSize: 13 }}>
          Limits for workspaces that haven’t bought a plan or whose plan ended. Paid plans set their own limits.
        </p>
        <div className="vr-grid vr-grid-4">
          {LIMIT_KEYS.map((k) => (
            <Field key={k} label={LIMIT_LABELS[k]} inputMode="numeric" value={String(L[k])} onChange={(e) => setLimits({ ...L, [k]: e.target.value as unknown as number })} />
          ))}
        </div>
        <hr className="vr-sep" />
        <div style={{ maxWidth: 260 }}>
          <Field label="Keep audit logs for (days)" inputMode="numeric" value={days ?? String(s.data.audit_retention_days)} onChange={(e) => setDays(e.target.value)} help="At least 30. Older entries are deleted by the worker." />
        </div>
        <div>
          <Button type="submit" variant="primary" busy={a.busy === "save"}>
            Save defaults
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
          <Field icon="search" placeholder="Filter by action prefix, e.g. admin. or user.login" value={action} onChange={(e) => setAction(e.target.value)} aria-label="Filter audit log" />
        </div>
        <Button type="submit">Filter</Button>
      </form>
      <Card padded={false}>
        <div className="vr-table-wrap">
          <table className="vr-table">
            <thead>
              <tr>
                <th>When</th>
                <th>Action</th>
                <th>Actor</th>
                <th>Target</th>
                <th>Details</th>
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
                  <td>{l.actor ?? <span className="vr-muted">System</span>}</td>
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
  if (!user.is_system_admin) return <Banner tone="danger">This area is for system administrators.</Banner>;
  return (
    <>
      <PageHeader eyebrow="System admin" title="Platform administration" subtitle="Plans, global limits, AI weights and accounts across every workspace. Every change here is audit-logged." />
      <nav className="vr-tabs" aria-label="Admin sections">
        {TABS.map((t) => (
          <button key={t} type="button" className={t === tab ? "is-on" : undefined} aria-current={t === tab ? "page" : undefined} onClick={() => router.replace(`/admin?tab=${t}`)}>
            {TAB_LABEL[t]}
          </button>
        ))}
      </nav>
      {tab === "overview" ? <Overview /> : tab === "users" ? <Users /> : tab === "workspaces" ? <Workspaces /> : tab === "plans" ? <Plans /> : tab === "multipliers" ? <Multipliers /> : tab === "settings" ? <Defaults /> : <Audit />}
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
