"use client";

import { t } from "@/i18n";
import { useParams, useRouter } from "next/navigation";
import { useState } from "react";

import { Badge, Banner, Button, Card, compact, money, PageHeader, Spinner } from "@/components/ui";
import { api, errorText, type Plan } from "@/lib/api";
import { useSession } from "@/lib/session";
import { useData } from "@/lib/use-data";

type Checkout = {
  id: string;
  kind: "subscription" | "package";
  status: string;
  amount_cents: number;
  currency: string;
  provider: string;
  plan: Plan | null;
  package: { name: string; tokens: number } | null;
};

/** The test provider's hosted checkout. With Stripe, customers pay on Stripe's own page instead. */
export default function CheckoutPage() {
  const { id } = useParams<{ id: string }>();
  const router = useRouter();
  const { refresh } = useSession();
  const c = useData<Checkout>(`/billing/checkout/${id}`);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (c.loading && !c.data) return <Spinner />;
  if (c.error) return <Banner tone="danger">{c.error.message}</Banner>;
  const co = c.data!;
  const isPackage = co.kind === "package";
  const done = isPackage ? "/billing/tokens?paid=1" : "/billing?paid=1";

  return (
    <>
      <PageHeader eyebrow={t("Checkout")} title={isPackage ? t("Token package: {name}", { name: co.package?.name }) : t("Verity {name}", { name: co.plan?.name })} />
      <Card className="vr-checkout" title={t("Order summary")} actions={<Badge tone="warn" dot>{t("Test mode")}</Badge>}>
        <div className="vr-form">
          <dl className="vr-kv">
            {isPackage ? (
              <>
                <dt>{t("Package")}</dt>
                <dd>{t("{package}, paid once", { package: co.package?.name ?? "" })}</dd>
                <dt>{t("AI tokens")}</dt>
                <dd className="vr-mono">{t("{tokens}, used after the plan’s allowance, expiring in 12 months", { tokens: compact(co.package?.tokens ?? 0) })}</dd>
              </>
            ) : (
              <>
                <dt>{t("Plan")}</dt>
                <dd>
                  {t("{plan}, billed every {cycle}", { plan: co.plan?.name ?? "", cycle: t(co.plan?.billing_cycle ?? "month") })}
                </dd>
                <dt>{t("AI tokens")}</dt>
                <dd className="vr-mono">
                  {t("{tokens} per {cycle}", { tokens: compact(co.plan?.token_allowance ?? 0), cycle: t(co.plan?.billing_cycle ?? "month") })}
                </dd>
              </>
            )}
            <dt>{t("Due today")}</dt>
            <dd>
              <b>{money(co.amount_cents, co.currency)}</b> <span className="vr-muted">{t("plus tax where it applies")}</span>
            </dd>
          </dl>
          <Banner tone="neutral" title={t("No card is charged.")}>
            {t("Payments run on a test provider until live billing is connected. Confirming reports the payment exactly as the payment provider would.")}
          </Banner>
          {error ? <Banner tone="danger">{error}</Banner> : null}
          {co.status === "paid" ? (
            <Banner title={isPackage ? t("Paid. The tokens are in your balance.") : t("Paid. Your plan is active.")} />
          ) : (
            <div className="vr-actions">
              <Button
                variant="primary"
                icon="lock"
                busy={busy}
                onClick={async () => {
                  setBusy(true);
                  setError(null);
                  try {
                    await api.post(`/billing/checkout/${id}/confirm`);
                    await refresh();
                    router.push(done);
                  } catch (e) {
                    setError(errorText(e));
                    setBusy(false);
                  }
                }}
              >
                {t("Pay")} {money(co.amount_cents, co.currency)}
              </Button>
              <Button variant="ghost" onClick={() => router.push(isPackage ? "/billing/tokens" : "/plans")}>
                {t("Cancel")}
              </Button>
            </div>
          )}
        </div>
      </Card>
    </>
  );
}
