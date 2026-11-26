"use client";

import type { Ticket, TicketStatus } from "@/lib/api";
import { PRIORITY, STATUS } from "@/lib/tickets";

import { Badge } from "./ui";

/** Ticket status: always a word, with a dot. */
export function StatusBadge({ status }: { status: TicketStatus }) {
  const s = STATUS[status];
  return (
    <Badge tone={s.tone} dot>
      {s.label}
    </Badge>
  );
}

export function PriorityBadge({ priority }: { priority: Ticket["priority"] }) {
  const p = PRIORITY[priority];
  return <Badge tone={p.tone}>{p.label}</Badge>;
}
