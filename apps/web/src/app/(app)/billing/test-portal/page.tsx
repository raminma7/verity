"use client";

import { t } from "@/i18n";
import Link from "next/link";
import { useState } from "react";

import { Badge, Banner, Button, Card, PageHeader, Segmented, Spinner } from "@/components/ui";
import { api, errorText } from "@/lib/api";
import { useData } from "@/lib/use-data";

import type { Billing } from "@/lib/billing";

/** The test provider's stand-in for Stripe's Customer Portal: switch the card on file to one that declines, and
 *  run a renewal now, to try failed payments without waiting a month. Only exists with the test provider. */
export default function TestPortal() {
  const b = useData<Billing>("/billing");
  const [busy, setBusy] = useState<string | null>(null);
  const [msg, setMsg] = useState<{ tone: "brand" | "danger"; text: string } | null>(null);

  async function run(name: string, fn: () => Promise<unknown>, done: string) {
    setBusy(name);
    setMsg(null);
    try {
      await fn();
      await b.reload();
      setMsg({ tone: "brand", text: done });
    } catch (e) {
      setMsg({ tone: "danger", text: errorText(e) });
    } finally {
      setBusy(null);
    }
  }

  if (b.loading && !b.data) return <Spinner />;
  if (b.error) return <Banner tone="danger">{b.error.message}</Banner>;
  const d = b.data!;
  if (d.provider !== "mock") return <Banner tone="neutral">{t("Payment methods and invoices are managed in Stripe’s billing portal.")}</Banner>;
  const card = d.account.test_card ?? "ok";

  return (
    <>
      <PageHeader eyebrow={t("Billing")} title={t("Payment methods")} subtitle={t("Test mode: this page stands in for the payment provider’s portal. No card details are entered or stored.")} />
      {msg ? <Banner tone={msg.tone}>{msg.text}</Banner> : null}
      <Card className="vr-test-portal" title={t("Card on file")} actions={<Badge tone="warn" dot>{t("Test mode")}</Badge>}>
        <div className="vr-form">
          <Segmented
            label={t("Test card")}
            value={card}
            onChange={(v) => run("card", () => api.put("/billing/test-card", { card: v }), v === "ok" ? "The test card now approves payments." : "The test card now declines payments.")}
            options={[
              { value: "ok", label: t("Test card that pays") },
              { value: "declines", label: t("Use a card that declines") },
            ]}
          />
          <span className="vr-help">
            {card === "ok" ? t("Renewals and upgrades go through.") : t("The next renewal or upgrade fails, as a declined card would.")}
          </span>
          {d.subscription && d.can_manage ? (
            <div className="vr-actions">
              <Button busy={busy === "renew"} onClick={() => run("renew", () => api.post("/dev/billing/renew"), "The renewal job ran for this workspace.")}>
                {t("Run the renewal now")}
              </Button>
            </div>
          ) : null}
          <Link href="/billing">{t("Back to Billing")}</Link>
        </div>
      </Card>
    </>
  );
}
