"use client";

import { t, msg } from "@/i18n";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";

import { Badge, Banner, Button, Card, compact, date, EmptyState, fmt, money, PageHeader, Spinner, type Tone } from "@/components/ui";
import { api, errorText } from "@/lib/api";
import { useSession } from "@/lib/session";
import { useData } from "@/lib/use-data";

type Packages = {
  packages: { id: string; key: string; name: string; description: string; tokens: number; price_cents: number; currency: string }[];
  grants: { id: string; name: string; tokens: number; consumed: number; remaining: number; status: "active" | "used_up" | "expired" | "revoked"; expires_at: string; created_at: string }[];
  remaining: number;
  next_expiry: string | null;
  can_buy: boolean;
};

const GRANT: Record<Packages["grants"][number]["status"], { tone: Tone; word: string }> = {
  active: { tone: "brand", word: msg("Available") },
  used_up: { tone: "neutral", word: msg("Used up") },
  expired: { tone: "neutral", word: msg("Expired") },
  revoked: { tone: "warn", word: msg("Refunded") },
};

function Tokens() {
  const p = useData<Packages>("/billing/packages");
  const { current, can } = useSession();
  const paid = useSearchParams().get("paid");
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const subCurrency = useData<{ subscription: { terms: { currency: string } } | null }>(can("billing.read") ? "/billing" : null).data?.subscription?.terms.currency;

  async function buy(id: string) {
    setBusy(id);
    setError(null);
    try {
      const r = await api.post<{ url: string }>(`/billing/packages/${id}/checkout`);
      window.location.assign(r.url);
    } catch (e) {
      setError(errorText(e));
      setBusy(null);
    }
  }

  if (p.loading && !p.data) return <Spinner />;
  if (p.error) return <Banner tone="danger">{p.error.message}</Banner>;
  const d = p.data!;
  const subscribed = !!current?.subscription?.active;

  return (
    <>
      <PageHeader
        eyebrow={t("Billing")}
        title={t("AI token packages")}
        subtitle={t("Extra weighted tokens for the whole workspace, paid once. They’re used after the plan’s allowance runs out, carry over from period to period and expire 12 months after purchase.")}
      />
      {paid ? <Banner title={t("Payment received. The tokens are in your balance.")}>{t("A receipt is on its way to the billing contacts.")}</Banner> : null}
      {error ? <Banner tone="danger">{error}</Banner> : null}
      {!subscribed ? (
        <Banner tone="neutral" title={t("Token packages need an active plan.")} action={<Link className="vr-btn vr-btn-secondary vr-btn-sm" href="/plans">{t("See plans")}</Link>}>
          {t("They add to a plan’s allowance.")}
        </Banner>
      ) : null}

      <div className="vr-grid vr-grid-2">
        <div className="vr-stat">
          <span className="vr-eyebrow">{t("Package tokens left")}</span>
          <span className="vr-display vr-stat-num">{compact(d.remaining)}</span>
          <span className="vr-help">{d.next_expiry ? t("Next expiry {next_expiry}", { next_expiry: date(d.next_expiry) }) : t("No package tokens yet")}</span>
        </div>
        <div className="vr-stat">
          <span className="vr-eyebrow">{t("Used first")}</span>
          <span className="vr-display vr-stat-num">{t("Plan")}</span>
          <span className="vr-help">
            {t("Package tokens start when this period’s allowance is used up.")} <Link href="/usage">{t("See usage")}</Link>
          </span>
        </div>
      </div>

      <section aria-label={t("Packages")}>
        {d.packages.length === 0 ? (
          <Card>
            <EmptyState icon="spark" title={t("No token packages on sale right now")} />
          </Card>
        ) : (
          <div className="vr-packages">
            {d.packages.map((pk) => {
              const other = !!subCurrency && pk.currency !== subCurrency;
              return (
                <article key={pk.id} className="vr-package" aria-label={pk.name}>
                  <h2 className="vr-h">{pk.name}</h2>
                  <span className="vr-display">{money(pk.price_cents, pk.currency)}</span>
                  <span className="vr-muted vr-caption">
                    {fmt(pk.tokens)} {t("weighted tokens · plus tax where it applies")}
                  </span>
                  {pk.description ? <p className="vr-muted" style={{ fontSize: 13 }}>{pk.description}</p> : null}
                  {other ? (
                    <span className="vr-help">
                      {t("Priced in")} {pk.currency}{t("; this workspace pays in")} {subCurrency}.
                    </span>
                  ) : d.can_buy ? (
                    <Button variant="primary" busy={busy === pk.id} disabled={!!busy} onClick={() => buy(pk.id)}>
                      {t("Buy")} {pk.name}
                    </Button>
                  ) : null}
                </article>
              );
            })}
          </div>
        )}
      </section>

      <Card title={t("Bought packages")} padded={false}>
        {d.grants.length === 0 ? (
          <EmptyState icon="file" title={t("No packages bought yet")} />
        ) : (
          <div className="vr-table-wrap">
            <table className="vr-table">
              <thead>
                <tr>
                  <th>{t("Bought")}</th>
                  <th>{t("Package")}</th>
                  <th>{t("Status")}</th>
                  <th className="num">{t("Used")}</th>
                  <th className="num">{t("Left")}</th>
                  <th>{t("Expires")}</th>
                </tr>
              </thead>
              <tbody>
                {d.grants.map((g) => (
                  <tr key={g.id}>
                    <td>{date(g.created_at)}</td>
                    <td>{g.name}</td>
                    <td>
                      <Badge tone={GRANT[g.status].tone} dot>
                        {t(GRANT[g.status].word)}
                      </Badge>
                    </td>
                    <td className="num">{fmt(g.consumed)}</td>
                    <td className="num">{fmt(g.remaining)}</td>
                    <td>{date(g.expires_at)}</td>
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
      <Tokens />
    </Suspense>
  );
}
