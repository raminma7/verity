"use client";

import { t } from "@/i18n";
import { useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";

import { Badge, Banner, Button, compact, date, Icon, money, PageHeader, Spinner } from "@/components/ui";
import { api, errorText, type Plan } from "@/lib/api";
import { LIMIT_KEYS, LIMIT_LABELS, limitText } from "@/lib/limits";
import { useSession } from "@/lib/session";
import { useData } from "@/lib/use-data";

function perMonth(p: Plan) {
  return p.billing_cycle === "year" ? p.price_cents / 12 : p.price_cents;
}

type Preview = {
  kind: "upgrade" | "downgrade";
  plan: Plan;
  currency: string;
  amount_due_cents: number;
  credit_cents: number;
  tax_cents: number | null;
  effective_at: string | null;
  tax_note: string | null;
};

function ChangeDialog({ preview, current, busy, error, onConfirm, onClose }: { preview: Preview; current: string; busy: boolean; error: string | null; onConfirm: () => void; onClose: () => void }) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    ref.current?.querySelector<HTMLButtonElement>("button.vr-btn-primary")?.focus();
    const esc = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", esc);
    return () => window.removeEventListener("keydown", esc);
  }, [onClose]);
  const p = preview.plan;
  const up = preview.kind === "upgrade";
  return (
    <div className="vr-dialog-backdrop" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className="vr-dialog" role="dialog" aria-modal="true" aria-labelledby="change-title" ref={ref}>
        <div className="vr-dialog-head">
          <h2 className="vr-h" id="change-title">
            {up ? t("Upgrade to {name}", { name: p.name }) : t("Switch to {name} at renewal", { name: p.name })}
          </h2>
        </div>
        <div className="vr-dialog-body">
          {up ? (
            <>
              <p className="vr-muted">
                {t("{plan} and its {tokens} tokens start today, with a new billing period. The unused part of {current} is credited.", { plan: p.name, tokens: compact(p.token_allowance), current })}
              </p>
              <dl className="vr-kv">
                <dt>{p.name}</dt>
                <dd className="vr-mono">{money(p.price_cents, p.currency)}</dd>
                <dt>{t("Unused")} {current}</dt>
                <dd className="vr-mono">−{money(preview.credit_cents, preview.currency)}</dd>
                {preview.tax_cents !== null ? (
                  <>
                    <dt>{t("Tax")}</dt>
                    <dd className="vr-mono">{money(preview.tax_cents, preview.currency)}</dd>
                  </>
                ) : null}
              </dl>
              <div className="vr-total">
                <span>{t("Due today")}</span>
                <span className="vr-mono">{money(preview.amount_due_cents + (preview.tax_cents ?? 0), preview.currency)}</span>
              </div>
              {preview.tax_note ? <span className="vr-help">{preview.tax_note}</span> : null}
              <span className="vr-help">{t("Charged to the payment method on file.")}</span>
            </>
          ) : (
            <p className="vr-muted">
              {t("You keep {current} and everything it includes until {date}. Then {plan} starts at {price} / {cycle}. Nothing is charged now.", { current, date: date(preview.effective_at), plan: p.name, price: money(p.price_cents, p.currency), cycle: t(p.billing_cycle) })}
            </p>
          )}
          {error ? <Banner tone="danger">{error}</Banner> : null}
          <div className="vr-actions">
            <Button variant="primary" busy={busy} onClick={onConfirm}>
              {up ? t("Pay and upgrade") : t("Switch at renewal")}
            </Button>
            <Button variant="ghost" onClick={onClose}>
              {t("Keep")} {current}
            </Button>
          </div>
        </div>
      </div>
    </div>
  );
}

export default function Plans() {
  const router = useRouter();
  const { current, can, refresh } = useSession();
  const plans = useData<Plan[]>("/plans");
  const billing = useData<{ account: { currency: string | null }; subscription: { status: string; terms: { currency: string } } | null }>(can("billing.read") ? "/billing" : null);
  const [busy, setBusy] = useState<string | null>(null);
  const [message, setMessage] = useState<{ tone: "brand" | "danger"; text: string } | null>(null);
  const [preview, setPreview] = useState<Preview | null>(null);
  const [dialogError, setDialogError] = useState<string | null>(null);
  const sub = current?.subscription?.active ? current.subscription : null;
  const currentPlan = plans.data?.find((p) => p.id === sub?.plan_id);
  const canBuy = can("billing.manage");
  const currency = billing.data?.account.currency ?? null;
  const pastDue = billing.data?.subscription?.status === "past_due";

  async function choose(p: Plan) {
    setBusy(p.id);
    setMessage(null);
    try {
      if (sub) {
        setDialogError(null);
        setPreview(await api.get<Preview>(`/billing/change-preview?plan_id=${p.id}`));
      } else {
        const r = await api.post<{ url: string }>("/billing/checkout", { plan_id: p.id });
        window.location.assign(r.url); // the provider's hosted checkout (the test page with the mock provider)
        return;
      }
    } catch (e) {
      setMessage({ tone: "danger", text: errorText(e) });
    }
    setBusy(null);
  }

  async function confirmChange() {
    if (!preview) return;
    setBusy("confirm");
    setDialogError(null);
    try {
      const r = await api.post<{ status: string; url?: string; effective_at?: string }>("/billing/change", { plan_id: preview.plan.id });
      if (r.status === "action_required" && r.url) {
        window.location.assign(r.url); // the bank wants the customer to confirm the payment
        return;
      }
      await refresh();
      if (r.status === "upgraded") {
        router.push(`/billing?upgraded=${encodeURIComponent(preview.plan.name)}`);
        return;
      }
      setPreview(null);
      setMessage({ tone: "brand", text: t("{name} starts when the current period ends on {effective_at}. You keep your current plan until then.", { name: preview.plan.name, effective_at: date(r.effective_at) }) });
      plans.reload();
    } catch (e) {
      setDialogError(errorText(e));
    }
    setBusy(null);
  }

  return (
    <>
      <PageHeader eyebrow={t("Plans")} title={t("Choose a plan")} subtitle={t("Every plan includes the knowledge base, chatbot, ticketing and email. They differ in AI tokens and resource limits. Tokens are shared across the whole workspace.")} />
      {!canBuy ? <Banner tone="neutral" title={t("Only people who manage billing can change the plan.")}>{t("Ask your workspace owner.")}</Banner> : null}
      {pastDue ? <Banner tone="danger" title={t("A payment is overdue.")}>{t("Settle it on the Billing page before changing plans.")}</Banner> : null}
      {message ? <Banner tone={message.tone}>{message.text}</Banner> : null}
      {plans.loading && !plans.data ? (
        <Spinner />
      ) : plans.error ? (
        <Banner tone="danger">{plans.error.message}</Banner>
      ) : (
        <div className="vr-plans">
          {plans.data!.map((p) => {
            const isCurrent = p.id === sub?.plan_id;
            const isDowngrade = currentPlan && perMonth(p) <= perMonth(currentPlan) && !isCurrent;
            const otherCurrency = !!currency && p.currency !== currency;
            return (
              <article key={p.id} className={isCurrent ? "vr-plan is-current" : otherCurrency ? "vr-plan is-other-currency" : "vr-plan"} aria-label={t("{name} plan", { name: p.name })}>
                <div className="vr-plan-head">
                  <h2 className="vr-h">{p.name}</h2>
                  {isCurrent ? <Badge tone="brand" dot>{t("Current plan")}</Badge> : null}
                </div>
                <div className="vr-plan-price">
                  <span className="vr-display">{money(p.price_cents, p.currency)}</span>
                  <span className="vr-muted">/ {p.billing_cycle}</span>
                </div>
                <p className="vr-muted" style={{ fontSize: 13, lineHeight: "20px" }}>
                  {p.description}
                </p>
                <div className="vr-plan-tokens">
                  <span>{t("AI tokens /")} {p.billing_cycle}</span>
                  <b className="vr-mono">{compact(p.token_allowance)}</b>
                </div>
                <ul>
                  {LIMIT_KEYS.map((k) => (
                    <li key={k}>
                      <Icon name="check" />
                      <span style={{ flex: 1 }}>{t(LIMIT_LABELS[k])}</span>
                      <span className="vr-mono" style={{ fontSize: 13 }}>
                        {limitText(k, p.limits[k] ?? 0)}
                      </span>
                    </li>
                  ))}
                </ul>
                {otherCurrency ? (
                  <span className="vr-help">
                    {t("Priced in {currency}; this workspace pays in {workspace_currency}.", { currency: p.currency, workspace_currency: currency })}
                  </span>
                ) : canBuy ? (
                  <Button variant={isCurrent ? "secondary" : "primary"} disabled={isCurrent || !!busy || pastDue} busy={busy === p.id} onClick={() => choose(p)}>
                    {isCurrent ? t("Your plan") : !sub ? t("Choose {name}", { name: p.name }) : isDowngrade ? t("Switch at renewal") : t("Upgrade to {name}", { name: p.name })}
                  </Button>
                ) : null}
              </article>
            );
          })}
        </div>
      )}
      <p className="vr-muted vr-caption">
        {t("Prices exclude tax, which is added at checkout from your billing address. Upgrades apply straight away and are prorated: unused time on the current plan is credited and a new billing period starts. Downgrades take effect at renewal. Unused plan tokens don’t roll over; bought token packages do.")}
      </p>
      {preview && currentPlan ? (
        <ChangeDialog
          preview={preview}
          current={currentPlan.name}
          busy={busy === "confirm"}
          error={dialogError}
          onConfirm={confirmChange}
          onClose={() => {
            setPreview(null);
            setBusy(null);
          }}
        />
      ) : null}
    </>
  );
}
