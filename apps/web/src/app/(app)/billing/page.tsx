"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";

import { Badge, Banner, Button, Card, date, EmptyState, money, PageHeader, Spinner, type Tone } from "@/components/ui";
import { api, errorText } from "@/lib/api";
import { useSession } from "@/lib/session";
import type { Billing, Payment } from "@/lib/billing";
import { useData } from "@/lib/use-data";

const KIND: Record<Payment["kind"], string> = { subscription: "Subscription", upgrade: "Upgrade", renewal: "Renewal", package: "Token package" };

function paymentStatus(p: Payment): { tone: Tone; word: string } {
  if (p.status === "succeeded") return p.attempt_count > 1 ? { tone: "brand", word: `Paid on attempt ${p.attempt_count}` } : { tone: "brand", word: "Paid" };
  if (p.status === "refunded") return { tone: "neutral", word: "Refunded" };
  if (p.status === "void") return { tone: "neutral", word: "Void" };
  return { tone: "danger", word: p.next_retry_at ? `Failed · retry ${date(p.next_retry_at)}` : "Failed" };
}

function subscriptionStatus(s: NonNullable<Billing["subscription"]>): { tone: Tone; word: string } {
  if (s.status === "past_due") return { tone: "danger", word: s.next_retry_at ? `Past due · retrying ${date(s.next_retry_at)}` : "Past due" };
  if (!s.active) return { tone: "neutral", word: "Ended" };
  if (s.cancel_at_period_end) return { tone: "warn", word: `Ends on ${date(s.current_period_end)}` };
  return { tone: "brand", word: "Active" };
}

function BillingPage() {
  const b = useData<Billing>("/billing");
  const { refresh } = useSession();
  const params = useSearchParams();
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  async function act(name: string, fn: () => Promise<unknown>, done?: string) {
    setBusy(name);
    setError(null);
    setNotice(null);
    try {
      await fn();
      await Promise.all([b.reload(), refresh()]);
      if (done) setNotice(done);
    } catch (e) {
      setError(errorText(e));
      await b.reload();
    } finally {
      setBusy(null);
    }
  }

  async function portal() {
    setBusy("portal");
    setError(null);
    try {
      const r = await api.post<{ url: string }>("/billing/portal");
      window.location.assign(r.url);
    } catch (e) {
      setError(errorText(e));
      setBusy(null);
    }
  }

  if (b.loading && !b.data) return <Spinner />;
  if (b.error) return <Banner tone="danger">{b.error.message}</Banner>;
  const { subscription: s, payments, can_manage, account } = b.data!;
  const st = s ? subscriptionStatus(s) : null;
  const pastDue = s?.status === "past_due";

  return (
    <>
      <PageHeader
        eyebrow="Billing"
        title="Subscription and billing"
        actions={
          can_manage ? (
            <div className="vr-actions">
              {account.has_portal ? (
                <Button variant="secondary" icon="card" busy={busy === "portal"} onClick={portal}>
                  Payment methods and invoices
                </Button>
              ) : null}
              <Link className="vr-btn vr-btn-secondary vr-btn-md" href="/plans">
                Change plan
              </Link>
            </div>
          ) : null
        }
      />
      {params.get("paid") ? <Banner title="Payment received. Your plan is active.">A receipt is on its way to the billing contacts.</Banner> : null}
      {params.get("upgraded") ? <Banner title={`Upgraded to ${params.get("upgraded")}.`}>The new plan and its token allowance start today. A receipt is on its way.</Banner> : null}
      {notice ? <Banner>{notice}</Banner> : null}
      {error ? <Banner tone="danger">{error}</Banner> : null}
      {pastDue ? (
        <Banner
          tone="danger"
          title="The last renewal payment failed."
          action={
            can_manage ? (
              <div className="vr-actions">
                {account.has_portal ? (
                  <Button size="sm" variant="secondary" busy={busy === "portal"} onClick={portal}>
                    Update payment method
                  </Button>
                ) : null}
                <Button size="sm" variant="primary" busy={busy === "retry"} onClick={() => act("retry", () => api.post("/billing/retry"), "Payment received. The subscription is active again.")}>
                  Retry payment now
                </Button>
              </div>
            ) : null
          }
        >
          AI answers are paused until it’s paid. Tickets, email and forms keep working until {date(s!.grace_ends_at)}; after that the subscription ends.
          {s!.next_retry_at ? ` We’ll try again on ${date(s!.next_retry_at)}.` : ""}
        </Banner>
      ) : null}

      <Card title="Current subscription" actions={st ? <Badge tone={st.tone} dot>{st.word}</Badge> : null}>
        {!s || (!s.active && !pastDue) ? (
          <EmptyState icon="card" title="No active subscription" action={can_manage ? <Link className="vr-btn vr-btn-primary vr-btn-md" href="/plans">Choose a plan</Link> : null}>
            {s ? `Your ${s.terms.plan_name} plan ended on ${date(s.current_period_end)}.` : "Pick a plan to turn on AI features and resource allowances."}
          </EmptyState>
        ) : (
          <div className="vr-form">
            <dl className="vr-kv">
              <dt>Plan</dt>
              <dd>{s.terms.plan_name}</dd>
              <dt>Price</dt>
              <dd>
                {money(s.terms.price_cents, s.terms.currency)} / {s.terms.billing_cycle} <span className="vr-muted">plus tax where it applies</span>
              </dd>
              <dt>Current period</dt>
              <dd>
                {date(s.current_period_start)} – {date(s.current_period_end)}
              </dd>
              <dt>{s.cancel_at_period_end ? "Ends on" : pastDue ? "Renewal due since" : "Renews on"}</dt>
              <dd>{date(pastDue ? s.past_due_since : s.current_period_end)}</dd>
              {s.pending_plan ? (
                <>
                  <dt>Scheduled change</dt>
                  <dd>
                    Switches to {s.pending_plan.name} on {date(s.current_period_end)}
                  </dd>
                </>
              ) : null}
              {account.currency ? (
                <>
                  <dt>Billing currency</dt>
                  <dd>{account.currency}</dd>
                </>
              ) : null}
            </dl>
            {can_manage && !pastDue ? (
              <div className="vr-actions">
                {s.cancel_at_period_end ? (
                  <Button variant="primary" busy={busy === "resume"} onClick={() => act("resume", () => api.post("/billing/resume"), "The subscription will renew as usual.")}>
                    Resume subscription
                  </Button>
                ) : (
                  <Button
                    variant="danger"
                    busy={busy === "cancel"}
                    onClick={() => {
                      if (confirm(`Cancel at the end of the period? Everything keeps working until ${date(s.current_period_end)}; then AI features and plan limits stop.`))
                        act("cancel", () => api.post("/billing/cancel"), `Cancelled. ${s.terms.plan_name} stays active until ${date(s.current_period_end)}.`);
                    }}
                  >
                    Cancel subscription
                  </Button>
                )}
                {s.pending_plan ? (
                  <Button variant="ghost" busy={busy === "pending"} onClick={() => act("pending", () => api.del("/billing/pending-change"), `You stay on ${s.terms.plan_name}.`)}>
                    Keep {s.terms.plan_name}
                  </Button>
                ) : null}
                <Link className="vr-btn vr-btn-ghost vr-btn-md" href="/billing/tokens">
                  Buy more AI tokens
                </Link>
              </div>
            ) : null}
          </div>
        )}
      </Card>

      <Card title="Billing history" padded={false}>
        {payments.length === 0 ? (
          <EmptyState icon="file" title="No invoices yet" />
        ) : (
          <div className="vr-table-wrap">
            <table className="vr-table">
              <thead>
                <tr>
                  <th>Date</th>
                  <th>Invoice</th>
                  <th>Description</th>
                  <th>Period</th>
                  <th>Status</th>
                  <th className="num">Tax</th>
                  <th className="num">Total</th>
                  <th>
                    <span className="sr-only">Documents</span>
                  </th>
                </tr>
              </thead>
              <tbody>
                {payments.map((p) => {
                  const ps = paymentStatus(p);
                  return (
                    <tr key={p.id}>
                      <td>{date(p.created_at)}</td>
                      <td className="vr-mono" style={{ fontSize: 12 }}>
                        {p.number ?? "—"}
                      </td>
                      <td>
                        {p.description}
                        <div className="vr-muted vr-caption">
                          {KIND[p.kind]}
                          {p.failure_message && p.status === "failed" ? ` · ${p.failure_message}` : ""}
                          {p.refunded_cents && p.status !== "refunded" ? ` · ${money(p.refunded_cents, p.currency)} refunded` : ""}
                        </div>
                      </td>
                      <td className="vr-muted">{p.kind === "package" || !p.period_start ? "—" : `${date(p.period_start)} – ${date(p.period_end)}`}</td>
                      <td>
                        <Badge tone={ps.tone} dot>
                          {ps.word}
                        </Badge>
                      </td>
                      <td className="num">{money(p.tax_cents, p.currency)}</td>
                      <td className="num">{money(p.amount_cents, p.currency)}</td>
                      <td>
                        {p.pdf_url ? (
                          <a href={p.pdf_url} target="_blank" rel="noreferrer">
                            PDF
                          </a>
                        ) : p.hosted_url ? (
                          <a href={p.hosted_url} target="_blank" rel="noreferrer">
                            View
                          </a>
                        ) : null}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </Card>
      <p className="vr-muted vr-caption">
        {b.data!.provider === "mock"
          ? "Test mode: payments run on a test provider and no card is charged. Invoice PDFs come with live billing."
          : "Prices exclude tax; tax is calculated at checkout from your billing address. Invoice PDFs and payment methods are in the payment portal."}
      </p>
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
