"use client";

import { t } from "@/i18n";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useState } from "react";

import { Banner, Card, PageHeader, Spinner } from "@/components/ui";
import { api } from "@/lib/api";
import { useSession } from "@/lib/session";

/** Back from Stripe Checkout: the payment is confirmed by Stripe's webhook, usually within seconds. */
function Return() {
  const id = useSearchParams().get("checkout");
  const router = useRouter();
  const { refresh } = useSession();
  const [slow, setSlow] = useState(false);
  const [failed, setFailed] = useState<string | null>(null);

  useEffect(() => {
    if (!id) return;
    let stop = false;
    const started = Date.now();
    async function poll() {
      while (!stop) {
        try {
          const c = await api.get<{ status: string; kind: string }>(`/billing/checkout/${id}`);
          if (c.status === "paid") {
            await refresh();
            router.replace(c.kind === "package" ? "/billing/tokens?paid=1" : "/billing?paid=1");
            return;
          }
          if (c.status === "expired" || c.status === "failed") {
            setFailed(c.status === "expired" ? t("This checkout expired before it was paid.") : t("The payment didn’t go through."));
            return;
          }
        } catch {
          /* keep waiting */
        }
        if (Date.now() - started > 20000) setSlow(true);
        await new Promise((r) => setTimeout(r, 1500));
      }
    }
    poll();
    return () => {
      stop = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id]);

  return (
    <>
      <PageHeader eyebrow={t("Checkout")} title={t("Confirming your payment")} />
      <Card>
        {failed ? (
          <Banner tone="danger" title={failed}>
            {t("Nothing was activated.")} <Link href="/plans">{t("Choose a plan to try again.")}</Link>
          </Banner>
        ) : (
          <div className="vr-form">
            <Spinner label={t("Waiting for the payment provider")} />
            {slow ? (
              <Banner tone="warn" title={t("This is taking longer than usual.")}>
                {t("Some payment methods take a few minutes to confirm. You can leave this page: the plan activates on its own and a receipt is emailed.")} <Link href="/billing">{t("Go to Billing")}</Link>
              </Banner>
            ) : null}
          </div>
        )}
      </Card>
    </>
  );
}

export default function Page() {
  return (
    <Suspense>
      <Return />
    </Suspense>
  );
}
