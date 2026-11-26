"use client";

import { useState } from "react";

import { Banner, Card, Switch } from "@/components/ui";
import { api, errorText } from "@/lib/api";
import { useSession } from "@/lib/session";

const PREFS = [
  { key: "usage_alerts", label: "AI usage alerts", help: "Email me when a workspace I manage reaches 80% and 90% of its token allowance." },
  { key: "billing", label: "Billing emails", help: "Receipts, renewals and failed payments for workspaces I manage." },
  { key: "product_updates", label: "Product updates", help: "Occasional news about new Verity features." },
];

export default function Notifications() {
  const { user, refresh } = useSession();
  const [prefs, setPrefs] = useState(user.notification_prefs);
  const [error, setError] = useState<string | null>(null);

  async function set(key: string, on: boolean) {
    const prev = prefs;
    setPrefs({ ...prefs, [key]: on });
    setError(null);
    try {
      await api.patch("/me", { notification_prefs: { [key]: on } });
      await refresh();
    } catch (e) {
      setPrefs(prev);
      setError(errorText(e));
    }
  }

  return (
    <Card title="Email notifications">
      {error ? <Banner tone="danger">{error}</Banner> : null}
      <div style={{ maxWidth: 640 }}>
        {PREFS.map((p) => (
          <Switch key={p.key} label={p.label} help={p.help} checked={prefs[p.key] ?? false} onChange={(on) => set(p.key, on)} />
        ))}
      </div>
      <p className="vr-help" style={{ marginTop: 8 }}>
        Security emails (verification, password resets, sign-in changes) are always sent.
      </p>
    </Card>
  );
}
