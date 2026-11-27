import type { Tone } from "@/components/ui";

import type { FormValue, TicketPriority, TicketStatus } from "./api";

/** Status and priority always show a word; the tone only supports it. */
export const STATUS: Record<TicketStatus, { label: string; tone: Tone }> = {
  new: { label: "New", tone: "brand" },
  open: { label: "Open", tone: "warn" },
  pending: { label: "Pending", tone: "neutral" },
  resolved: { label: "Resolved", tone: "neutral" },
  closed: { label: "Closed", tone: "neutral" },
};

export const PRIORITY: Record<TicketPriority, { label: string; tone: Tone }> = {
  urgent: { label: "Urgent", tone: "danger" },
  high: { label: "High", tone: "warn" },
  normal: { label: "Normal", tone: "neutral" },
  low: { label: "Low", tone: "neutral" },
};

export const SOURCE: Record<string, string> = {
  chatbot_form: "Chatbot form",
  conversation: "Chat conversation",
  manual: "Added by the team",
  email: "Email",
};

export const STATUS_ORDER: TicketStatus[] = ["new", "open", "pending", "resolved", "closed"];
export const PRIORITY_ORDER: TicketPriority[] = ["urgent", "high", "normal", "low"];

export function showValue(v: FormValue | undefined): string {
  if (v === true) return "Yes";
  if (v === false) return "No";
  if (Array.isArray(v)) return v.join(", ");
  return v === null || v === undefined || v === "" ? "—" : String(v);
}
