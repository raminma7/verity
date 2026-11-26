"use client";

import { useParams, useRouter } from "next/navigation";
import { useState } from "react";

import { Badge, Banner, Button, Card, compact, money, PageHeader, Spinner } from "@/components/ui";
import { api, errorText, type Plan } from "@/lib/api";
import { useSession } from "@/lib/session";
import { useData } from "@/lib/use-data";

type Checkout = { id: string; status: string; amount_cents: number; currency: string; provider: string; plan: Plan };

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

  return (
    <>
      <PageHeader eyebrow="Checkout" title={`Verity ${co.plan.name}`} />
      <Card className="vr-checkout" title="Order summary" actions={<Badge tone="warn" dot>Test mode</Badge>}>
        <div className="vr-form">
          <dl className="vr-kv">
            <dt>Plan</dt>
            <dd>{co.plan.name}, billed every {co.plan.billing_cycle}</dd>
            <dt>AI tokens</dt>
            <dd className="vr-mono">{compact(co.plan.token_allowance)} per {co.plan.billing_cycle}</dd>
            <dt>Due today</dt>
            <dd>
              <b>{money(co.amount_cents, co.currency)}</b>
            </dd>
          </dl>
          <Banner tone="neutral" title="No card is charged.">
            Payments run on a test provider until live billing is connected. Confirming activates the plan exactly as a real payment would.
          </Banner>
          {error ? <Banner tone="danger">{error}</Banner> : null}
          {co.status === "paid" ? (
            <Banner title="Paid. Your plan is active." />
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
                    router.push("/billing?paid=1");
                  } catch (e) {
                    setError(errorText(e));
                    setBusy(false);
                  }
                }}
              >
                Pay {money(co.amount_cents, co.currency)}
              </Button>
              <Button variant="ghost" onClick={() => router.push("/plans")}>
                Cancel
              </Button>
            </div>
          )}
        </div>
      </Card>
    </>
  );
}
