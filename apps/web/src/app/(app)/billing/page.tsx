"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";

import { Badge, Banner, Button, Card, date, EmptyState, money, PageHeader, Spinner } from "@/components/ui";
import { api, errorText } from "@/lib/api";
import { useSession } from "@/lib/session";
import { useData } from "@/lib/use-data";

type Billing = {
  subscription: null | {
    active: boolean;
    status: string;
    plan_id: string;
    terms: { plan_name: string; price_cents: number; currency: string; billing_cycle: string; token_allowance: number };
    current_period_start: string;
    current_period_end: string;
    cancel_at_period_end: boolean;
    pending_plan: { id: string; name: string } | null;
  };
  payments: { id: string; description: string; amount_cents: number; currency: string; status: string; period_start: string | null; period_end: string | null; created_at: string }[];
  can_manage: boolean;
};

function BillingPage() {
  const b = useData<Billing>("/billing");
  const { refresh } = useSession();
  const paid = useSearchParams().get("paid");
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function act(name: string, fn: () => Promise<unknown>) {
    setBusy(name);
    setError(null);
    try {
      await fn();
      await Promise.all([b.reload(), refresh()]);
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(null);
    }
  }

  if (b.loading && !b.data) return <Spinner />;
  if (b.error) return <Banner tone="danger">{b.error.message}</Banner>;
  const { subscription: s, payments, can_manage } = b.data!;

  return (
    <>
      <PageHeader eyebrow="Billing" title="Subscription and billing" actions={can_manage ? <Link className="vr-btn vr-btn-secondary vr-btn-md" href="/plans">Change plan</Link> : null} />
      {paid ? <Banner title="Payment received. Your plan is active.">A receipt is on its way to the billing contacts.</Banner> : null}
      {error ? <Banner tone="danger">{error}</Banner> : null}

      <Card title="Current subscription">
        {!s || !s.active ? (
          <EmptyState icon="card" title="No active subscription" action={can_manage ? <Link className="vr-btn vr-btn-primary vr-btn-md" href="/plans">Choose a plan</Link> : null}>
            {s ? `Your ${s.terms.plan_name} plan ended on ${date(s.current_period_end)}.` : "Pick a plan to turn on AI features and resource allowances."}
          </EmptyState>
        ) : (
          <div className="vr-form">
            <dl className="vr-kv">
              <dt>Plan</dt>
              <dd style={{ display: "flex", gap: 8, alignItems: "center" }}>
                {s.terms.plan_name}
                {s.cancel_at_period_end ? <Badge tone="warn" dot>Cancels at period end</Badge> : <Badge tone="brand" dot>Active</Badge>}
              </dd>
              <dt>Price</dt>
              <dd>
                {money(s.terms.price_cents, s.terms.currency)} / {s.terms.billing_cycle}
              </dd>
              <dt>Current period</dt>
              <dd>
                {date(s.current_period_start)} – {date(s.current_period_end)}
              </dd>
              <dt>{s.cancel_at_period_end ? "Ends on" : "Renews on"}</dt>
              <dd>{date(s.current_period_end)}</dd>
              {s.pending_plan ? (
                <>
                  <dt>Scheduled change</dt>
                  <dd>
                    Switches to {s.pending_plan.name} on {date(s.current_period_end)}
                  </dd>
                </>
              ) : null}
            </dl>
            {can_manage ? (
              <div className="vr-actions">
                {s.cancel_at_period_end ? (
                  <Button variant="primary" busy={busy === "resume"} onClick={() => act("resume", () => api.post("/billing/resume"))}>
                    Resume subscription
                  </Button>
                ) : (
                  <Button
                    variant="danger"
                    busy={busy === "cancel"}
                    onClick={() => {
                      if (confirm(`Cancel at the end of the period? AI features stop after ${date(s.current_period_end)}.`)) act("cancel", () => api.post("/billing/cancel"));
                    }}
                  >
                    Cancel subscription
                  </Button>
                )}
                {s.pending_plan ? (
                  <Button variant="ghost" busy={busy === "pending"} onClick={() => act("pending", () => api.del("/billing/pending-change"))}>
                    Keep {s.terms.plan_name}
                  </Button>
                ) : null}
              </div>
            ) : null}
          </div>
        )}
      </Card>

      <Card title="Billing history" padded={false}>
        {payments.length === 0 ? (
          <EmptyState icon="file" title="No payments yet" />
        ) : (
          <div className="vr-table-wrap">
            <table className="vr-table">
              <thead>
                <tr>
                  <th>Date</th>
                  <th>Description</th>
                  <th>Period</th>
                  <th>Status</th>
                  <th className="num">Amount</th>
                </tr>
              </thead>
              <tbody>
                {payments.map((p) => (
                  <tr key={p.id}>
                    <td>{date(p.created_at)}</td>
                    <td>{p.description}</td>
                    <td className="vr-muted">
                      {date(p.period_start)} – {date(p.period_end)}
                    </td>
                    <td>
                      <Badge tone={p.status === "succeeded" ? "brand" : "danger"} dot>
                        {p.status === "succeeded" ? "Paid" : p.status}
                      </Badge>
                    </td>
                    <td className="num">{money(p.amount_cents, p.currency)}</td>
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

export default function Page() {
  return (
    <Suspense>
      <BillingPage />
    </Suspense>
  );
}
