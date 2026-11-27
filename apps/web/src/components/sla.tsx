"use client";

import { useEffect, useState } from "react";

import { minutes } from "@/components/charts";
import { Banner, Button, Card, Field, Spinner } from "@/components/ui";
import { api, errorText } from "@/lib/api";
import { useData } from "@/lib/use-data";

type Targets = Record<string, { first_response_minutes: number; resolution_minutes: number }>;
const PRIORITIES = [
  ["urgent", "Urgent"],
  ["high", "High"],
  ["normal", "Normal"],
  ["low", "Low"],
] as const;

/** Settings → Tickets: response and resolution targets per priority, in hours (stored as minutes). */
export function SlaTargets() {
  const d = useData<{ targets: Targets; defaults: Targets; can_edit: boolean }>("/sla-targets");
  const [form, setForm] = useState<Record<string, { frt: string; res: string }>>({});
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<{ tone: "brand" | "danger"; text: string } | null>(null);

  useEffect(() => {
    if (!d.data) return;
    setForm(Object.fromEntries(PRIORITIES.map(([p]) => [p, { frt: String(d.data!.targets[p].first_response_minutes / 60), res: String(d.data!.targets[p].resolution_minutes / 60) }])));
  }, [d.data]);

  if (d.loading && !d.data) return <Spinner />;
  if (d.error) return <Banner tone="danger">{d.error.message}</Banner>;
  const edit = d.data!.can_edit;

  async function save(values: Record<string, { frt: string; res: string }>, done: string) {
    setBusy(true);
    setMsg(null);
    try {
      const targets = Object.fromEntries(
        PRIORITIES.map(([p]) => [p, { first_response_minutes: Math.round(Number(values[p].frt) * 60), resolution_minutes: Math.round(Number(values[p].res) * 60) }]),
      );
      await api.put("/sla-targets", { targets });
      await d.reload();
      setMsg({ tone: "brand", text: done });
    } catch (e) {
      setMsg({ tone: "danger", text: errorText(e) });
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card title="Response targets (SLA)">
      <div className="vr-form">
        <p className="vr-muted" style={{ margin: 0 }}>
          How quickly the team should first answer and resolve a ticket, by priority, in hours around the clock. Analytics counts a ticket as breached once it passes a target; changing a target recounts past tickets too.
        </p>
        {msg ? <Banner tone={msg.tone}>{msg.text}</Banner> : null}
        <div className="vr-table-wrap">
          <table className="vr-table">
            <thead>
              <tr>
                <th>Priority</th>
                <th>First response within (hours)</th>
                <th>Resolved within (hours)</th>
              </tr>
            </thead>
            <tbody>
              {PRIORITIES.map(([p, label]) => (
                <tr key={p}>
                  <td>{label}</td>
                  <td>
                    {edit ? (
                      <Field aria-label={`${label}: first response within, in hours`} type="number" min={0.1} step="any" value={form[p]?.frt ?? ""} onChange={(e) => setForm({ ...form, [p]: { ...form[p], frt: e.target.value } })} />
                    ) : (
                      minutes(d.data!.targets[p].first_response_minutes)
                    )}
                  </td>
                  <td>
                    {edit ? (
                      <Field aria-label={`${label}: resolved within, in hours`} type="number" min={0.1} step="any" value={form[p]?.res ?? ""} onChange={(e) => setForm({ ...form, [p]: { ...form[p], res: e.target.value } })} />
                    ) : (
                      minutes(d.data!.targets[p].resolution_minutes)
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {edit ? (
          <div className="vr-actions">
            <Button variant="primary" busy={busy} onClick={() => save(form, "Targets saved. Analytics uses them from now on, for past tickets too.")}>
              Save targets
            </Button>
            <Button
              variant="ghost"
              disabled={busy}
              onClick={() => {
                const defaults = Object.fromEntries(PRIORITIES.map(([p]) => [p, { frt: String(d.data!.defaults[p].first_response_minutes / 60), res: String(d.data!.defaults[p].resolution_minutes / 60) }]));
                save(defaults, "Default targets restored.");
              }}
            >
              Restore defaults
            </Button>
          </div>
        ) : null}
      </div>
    </Card>
  );
}
