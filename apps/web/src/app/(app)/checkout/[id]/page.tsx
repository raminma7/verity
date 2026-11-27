"use client";

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
      <PageHeader eyebrow="Checkout" title={isPackage ? `Token package: ${co.package?.name}` : `Verity ${co.plan?.name}`} />
      <Card className="vr-checkout" title="Order summary" actions={<Badge tone="warn" dot>Test mode</Badge>}>
        <div className="vr-form">
          <dl className="vr-kv">
            {isPackage ? (
              <>
                <dt>Package</dt>
                <dd>{co.package?.name}, paid once</dd>
                <dt>AI tokens</dt>
                <dd className="vr-mono">{compact(co.package?.tokens ?? 0)}, used after the plan’s allowance, expiring in 12 months</dd>
              </>
            ) : (
              <>
                <dt>Plan</dt>
                <dd>
                  {co.plan?.name}, billed every {co.plan?.billing_cycle}
                </dd>
                <dt>AI tokens</dt>
                <dd className="vr-mono">
                  {compact(co.plan?.token_allowance ?? 0)} per {co.plan?.billing_cycle}
                </dd>
              </>
            )}
            <dt>Due today</dt>
            <dd>
              <b>{money(co.amount_cents, co.currency)}</b> <span className="vr-muted">plus tax where it applies</span>
            </dd>
          </dl>
          <Banner tone="neutral" title="No card is charged.">
            Payments run on a test provider until live billing is connected. Confirming reports the payment exactly as the payment provider would.
          </Banner>
          {error ? <Banner tone="danger">{error}</Banner> : null}
          {co.status === "paid" ? (
            <Banner title={isPackage ? "Paid. The tokens are in your balance." : "Paid. Your plan is active."} />
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
                Pay {money(co.amount_cents, co.currency)}
              </Button>
              <Button variant="ghost" onClick={() => router.push(isPackage ? "/billing/tokens" : "/plans")}>
                Cancel
              </Button>
            </div>
          )}
        </div>
      </Card>
    </>
  );
}
