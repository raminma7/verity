"use client";

import { t, msg } from "@/i18n";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";

import { Badge, Banner, Button, Card, date, EmptyState, money, PageHeader, Spinner, type Tone } from "@/components/ui";
import { api, errorText } from "@/lib/api";
import { useSession } from "@/lib/session";
import type { Billing, Payment } from "@/lib/billing";
import { useData } from "@/lib/use-data";

const KIND: Record<Payment["kind"], string> = { subscription: msg("Subscription"), upgrade: msg("Upgrade"), renewal: msg("Renewal"), package: msg("Token package") };

function paymentStatus(p: Payment): { tone: Tone; word: string } {
  if (p.status === "succeeded") return p.attempt_count > 1 ? { tone: "brand", word: t("Paid on attempt {attempt_count}", { attempt_count: p.attempt_count }) } : { tone: "brand", word: t("Paid") };
  if (p.status === "refunded") return { tone: "neutral", word: t("Refunded") };
  if (p.status === "void") return { tone: "neutral", word: t("Void") };
  return { tone: "danger", word: p.next_retry_at ? t("Failed · retry {next_retry_at}", { next_retry_at: date(p.next_retry_at) }) : t("Failed") };
}

function subscriptionStatus(s: NonNullable<Billing["subscription"]>): { tone: Tone; word: string } {
  if (s.status === "past_due") return { tone: "danger", word: s.next_retry_at ? t("Past due · retrying {next_retry_at}", { next_retry_at: date(s.next_retry_at) }) : t("Past due") };
  if (!s.active) return { tone: "neutral", word: t("Ended") };
  if (s.cancel_at_period_end) return { tone: "warn", word: t("Ends on {current_period_end}", { current_period_end: date(s.current_period_end) }) };
  return { tone: "brand", word: t("Active") };
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
        eyebrow={t("Billing")}
        title={t("Subscription and billing")}
        actions={
          can_manage ? (
            <div className="vr-actions">
              {account.has_portal ? (
                <Button variant="secondary" icon="card" busy={busy === "portal"} onClick={portal}>
                  {t("Payment methods and invoices")}
                </Button>
              ) : null}
              <Link className="vr-btn vr-btn-secondary vr-btn-md" href="/plans">
                {t("Change plan")}
              </Link>
            </div>
          ) : null
        }
      />
      {params.get("paid") ? <Banner title={t("Payment received. Your plan is active.")}>{t("A receipt is on its way to the billing contacts.")}</Banner> : null}
      {params.get("upgraded") ? <Banner title={t("Upgraded to {v}.", { v: params.get("upgraded") })}>{t("The new plan and its token allowance start today. A receipt is on its way.")}</Banner> : null}
      {notice ? <Banner>{notice}</Banner> : null}
      {error ? <Banner tone="danger">{error}</Banner> : null}
      {pastDue ? (
        <Banner
          tone="danger"
          title={t("The last renewal payment failed.")}
          action={
            can_manage ? (
              <div className="vr-actions">
                {account.has_portal ? (
                  <Button size="sm" variant="secondary" busy={busy === "portal"} onClick={portal}>
                    {t("Update payment method")}
                  </Button>
                ) : null}
                <Button size="sm" variant="primary" busy={busy === "retry"} onClick={() => act("retry", () => api.post("/billing/retry"), t("Payment received. The subscription is active again."))}>
                  {t("Retry payment now")}
                </Button>
              </div>
            ) : null
          }
        >
          {t("AI answers are paused until it’s paid. Tickets, email and forms keep working until {date}; after that the subscription ends.", { date: date(s!.grace_ends_at) })}
          {s!.next_retry_at ? ` ${t("We’ll try again on {date}.", { date: date(s!.next_retry_at) })}` : ""}
        </Banner>
      ) : null}

      <Card title={t("Current subscription")} actions={st ? <Badge tone={st.tone} dot>{t(st.word)}</Badge> : null}>
        {!s || (!s.active && !pastDue) ? (
          <EmptyState icon="card" title={t("No active subscription")} action={can_manage ? <Link className="vr-btn vr-btn-primary vr-btn-md" href="/plans">{t("Choose a plan")}</Link> : null}>
            {s ? t("Your {plan_name} plan ended on {current_period_end}.", { plan_name: s.terms.plan_name, current_period_end: date(s.current_period_end) }) : t("Pick a plan to turn on AI features and resource allowances.")}
          </EmptyState>
        ) : (
          <div className="vr-form">
            <dl className="vr-kv">
              <dt>{t("Plan")}</dt>
              <dd>{s.terms.plan_name}</dd>
              <dt>{t("Price")}</dt>
              <dd>
                {money(s.terms.price_cents, s.terms.currency)} / {s.terms.billing_cycle} <span className="vr-muted">{t("plus tax where it applies")}</span>
              </dd>
              <dt>{t("Current period")}</dt>
              <dd>
                {date(s.current_period_start)} – {date(s.current_period_end)}
              </dd>
              <dt>{s.cancel_at_period_end ? t("Ends on") : pastDue ? t("Renewal due since") : t("Renews on")}</dt>
              <dd>{date(pastDue ? s.past_due_since : s.current_period_end)}</dd>
              {s.pending_plan ? (
                <>
                  <dt>{t("Scheduled change")}</dt>
                  <dd>
                    {t("Switches to {plan} on {date}", { plan: s.pending_plan.name, date: date(s.current_period_end) })}
                  </dd>
                </>
              ) : null}
              {account.currency ? (
                <>
                  <dt>{t("Billing currency")}</dt>
                  <dd>{account.currency}</dd>
                </>
              ) : null}
            </dl>
            {can_manage && !pastDue ? (
              <div className="vr-actions">
                {s.cancel_at_period_end ? (
                  <Button variant="primary" busy={busy === "resume"} onClick={() => act("resume", () => api.post("/billing/resume"), t("The subscription will renew as usual."))}>
                    {t("Resume subscription")}
                  </Button>
                ) : (
                  <Button
                    variant="danger"
                    busy={busy === "cancel"}
                    onClick={() => {
                      if (confirm(t("Cancel at the end of the period? Everything keeps working until {current_period_end}; then AI features and plan limits stop.", { current_period_end: date(s.current_period_end) })))
                        act("cancel", () => api.post("/billing/cancel"), t("Cancelled. {plan_name} stays active until {current_period_end}.", { plan_name: s.terms.plan_name, current_period_end: date(s.current_period_end) }));
                    }}
                  >
                    {t("Cancel subscription")}
                  </Button>
                )}
                {s.pending_plan ? (
                  <Button variant="ghost" busy={busy === "pending"} onClick={() => act("pending", () => api.del("/billing/pending-change"), t("You stay on {plan_name}.", { plan_name: s.terms.plan_name }))}>
                    {t("Keep")} {s.terms.plan_name}
                  </Button>
                ) : null}
                <Link className="vr-btn vr-btn-ghost vr-btn-md" href="/billing/tokens">
                  {t("Buy more AI tokens")}
                </Link>
              </div>
            ) : null}
          </div>
        )}
      </Card>

      <Card title={t("Billing history")} padded={false}>
        {payments.length === 0 ? (
          <EmptyState icon="file" title={t("No invoices yet")} />
        ) : (
          <div className="vr-table-wrap">
            <table className="vr-table">
              <thead>
                <tr>
                  <th>{t("Date")}</th>
                  <th>{t("Invoice")}</th>
                  <th>{t("Description")}</th>
                  <th>{t("Period")}</th>
                  <th>{t("Status")}</th>
                  <th className="num">{t("Tax")}</th>
                  <th className="num">{t("Total")}</th>
                  <th>
                    <span className="sr-only">{t("Documents")}</span>
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
                          {t(KIND[p.kind])}
                          {p.failure_message && p.status === "failed" ? ` · ${p.failure_message}` : ""}
                          {p.refunded_cents && p.status !== "refunded" ? ` · ${t("{amount} refunded", { amount: money(p.refunded_cents, p.currency) })}` : ""}
                        </div>
                      </td>
                      <td className="vr-muted">{p.kind === "package" || !p.period_start ? "—" : `${date(p.period_start)} – ${date(p.period_end)}`}</td>
                      <td>
                        <Badge tone={ps.tone} dot>
                          {t(ps.word)}
                        </Badge>
                      </td>
                      <td className="num">{money(p.tax_cents, p.currency)}</td>
                      <td className="num">{money(p.amount_cents, p.currency)}</td>
                      <td>
                        {p.pdf_url ? (
                          <a href={p.pdf_url} target="_blank" rel="noreferrer">
                            {t("PDF")}
                          </a>
                        ) : p.hosted_url ? (
                          <a href={p.hosted_url} target="_blank" rel="noreferrer">
                            {t("View")}
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
          ? t("Test mode: payments run on a test provider and no card is charged. Invoice PDFs come with live billing.")
          : t("Prices exclude tax; tax is calculated at checkout from your billing address. Invoice PDFs and payment methods are in the payment portal.")}
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
