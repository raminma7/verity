"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";

import { Badge, Banner, Button, compact, date, Icon, money, PageHeader, Spinner } from "@/components/ui";
import { api, errorText, type Plan } from "@/lib/api";
import { LIMIT_KEYS, LIMIT_LABELS, limitText } from "@/lib/limits";
import { useSession } from "@/lib/session";
import { useData } from "@/lib/use-data";

function perMonth(p: Plan) {
  return p.billing_cycle === "year" ? p.price_cents / 12 : p.price_cents;
}

export default function Plans() {
  const router = useRouter();
  const { current, can, refresh } = useSession();
  const plans = useData<Plan[]>("/plans");
  const [busy, setBusy] = useState<string | null>(null);
  const [message, setMessage] = useState<{ tone: "brand" | "danger"; text: string } | null>(null);
  const sub = current?.subscription?.active ? current.subscription : null;
  const currentPlan = plans.data?.find((p) => p.id === sub?.plan_id);
  const canBuy = can("billing.manage");

  async function choose(p: Plan) {
    setBusy(p.id);
    setMessage(null);
    try {
      const r = await api.post<{ scheduled: boolean; checkout_id?: string; effective_at?: string }>("/billing/checkout", { plan_id: p.id });
      if (r.scheduled) {
        setMessage({ tone: "brand", text: `${p.name} starts when the current period ends on ${date(r.effective_at)}. You keep your current plan until then.` });
        await refresh();
      } else router.push(`/checkout/${r.checkout_id}`);
    } catch (e) {
      setMessage({ tone: "danger", text: errorText(e) });
    } finally {
      setBusy(null);
    }
  }

  return (
    <>
      <PageHeader eyebrow="Plans" title="Choose a plan" subtitle="Every plan includes the knowledge base, chatbot, ticketing and email. They differ in AI tokens and resource limits. Tokens are shared across the whole workspace." />
      {!canBuy ? <Banner tone="neutral" title="Only people who manage billing can change the plan.">Ask your workspace owner.</Banner> : null}
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
            return (
              <article key={p.id} className={isCurrent ? "vr-plan is-current" : "vr-plan"} aria-label={`${p.name} plan`}>
                <div className="vr-plan-head">
                  <h2 className="vr-h">{p.name}</h2>
                  {isCurrent ? <Badge tone="brand" dot>Current plan</Badge> : null}
                </div>
                <div className="vr-plan-price">
                  <span className="vr-display">{money(p.price_cents, p.currency)}</span>
                  <span className="vr-muted">/ {p.billing_cycle}</span>
                </div>
                <p className="vr-muted" style={{ fontSize: 13, lineHeight: "20px" }}>
                  {p.description}
                </p>
                <div className="vr-plan-tokens">
                  <span>AI tokens / {p.billing_cycle}</span>
                  <b className="vr-mono">{compact(p.token_allowance)}</b>
                </div>
                <ul>
                  {LIMIT_KEYS.map((k) => (
                    <li key={k}>
                      <Icon name="check" />
                      <span style={{ flex: 1 }}>{LIMIT_LABELS[k]}</span>
                      <span className="vr-mono" style={{ fontSize: 13 }}>
                        {limitText(k, p.limits[k] ?? 0)}
                      </span>
                    </li>
                  ))}
                </ul>
                {canBuy ? (
                  <Button variant={isCurrent ? "secondary" : "primary"} disabled={isCurrent || !!busy} busy={busy === p.id} onClick={() => choose(p)}>
                    {isCurrent ? "Your plan" : !sub ? `Choose ${p.name}` : isDowngrade ? `Switch at renewal` : `Upgrade to ${p.name}`}
                  </Button>
                ) : null}
              </article>
            );
          })}
        </div>
      )}
      <p className="vr-muted vr-caption">
        Prices exclude tax. Upgrades start a new billing period straight away; downgrades take effect at renewal. Unused tokens don’t roll over.
      </p>
    </>
  );
}
