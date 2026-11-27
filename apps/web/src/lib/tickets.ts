import { msg } from "@/i18n";
import type { Tone } from "@/components/ui";

import type { FormValue, TicketPriority, TicketStatus } from "./api";

/** Status and priority always show a word; the tone only supports it. */
export const STATUS: Record<TicketStatus, { label: string; tone: Tone }> = {
  new: { label: msg("New"), tone: "brand" },
  open: { label: msg("Open"), tone: "warn" },
  pending: { label: msg("Pending"), tone: "neutral" },
  resolved: { label: msg("Resolved"), tone: "neutral" },
  closed: { label: msg("Closed"), tone: "neutral" },
};

export const PRIORITY: Record<TicketPriority, { label: string; tone: Tone }> = {
  urgent: { label: msg("Urgent"), tone: "danger" },
  high: { label: msg("High"), tone: "warn" },
  normal: { label: msg("Normal"), tone: "neutral" },
  low: { label: msg("Low"), tone: "neutral" },
};

export const SOURCE: Record<string, string> = {
  chatbot_form: msg("Chatbot form"),
  conversation: msg("Chat conversation"),
  manual: msg("Added by the team"),
  email: msg("Email"),
};

export const STATUS_ORDER: TicketStatus[] = ["new", "open", "pending", "resolved", "closed"];
export const PRIORITY_ORDER: TicketPriority[] = ["urgent", "high", "normal", "low"];

export function showValue(v: FormValue | undefined): string {
  if (v === true) return "Yes";
  if (v === false) return "No";
  if (Array.isArray(v)) return v.join(", ");
  return v === null || v === undefined || v === "" ? "—" : String(v);
}
