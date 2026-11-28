"use client";

import { useState } from "react";

import { t } from "@/i18n";
import { api, errorText, type Ticket, type TicketStatus } from "@/lib/api";
import { PRIORITY, STATUS } from "@/lib/tickets";

import { Badge, Button, dateTime } from "./ui";

/** Ticket status: always a word, with a dot. */
export function StatusBadge({ status }: { status: TicketStatus }) {
  const s = STATUS[status];
  return (
    <Badge tone={s.tone} dot>
      {t(s.label)}
    </Badge>
  );
}

export function PriorityBadge({ priority }: { priority: Ticket["priority"] }) {
  const p = PRIORITY[priority];
  return <Badge tone={p.tone}>{t(p.label)}</Badge>;
}

/** An AI summary of the case for the team (Phase 6). Customers never see it; it needs AI tokens. */
export function TicketSummary({ id, summary, onDone }: { id: string; summary: { text: string; at: string } | null; onDone: () => Promise<void> | void }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  return (
    <div className="vr-summary" aria-label={t("Summary")}>
      <div className="vr-summary-head">
        <b>{t("Summary")}</b>
        {summary ? <span className="vr-muted vr-caption">{t("AI · updated {when}", { when: dateTime(summary.at) })}</span> : null}
        <Button
          size="sm"
          variant="ghost"
          icon="spark"
          busy={busy}
          onClick={async () => {
            setBusy(true);
            setError(null);
            try {
              await api.post(`/tickets/${id}/summary`);
              await onDone();
            } catch (e) {
              setError(errorText(e));
            } finally {
              setBusy(false);
            }
          }}
        >
          {summary ? t("Update summary") : t("Summarize")}
        </Button>
      </div>
      {summary ? <p>{summary.text}</p> : <p className="vr-muted">{t("A few lines on what the customer needs and what’s still open, written by AI for the team.")}</p>}
      {error ? <span className="vr-word-danger vr-caption">{error}</span> : null}
    </div>
  );
}
