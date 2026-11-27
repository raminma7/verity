"use client";

import { t, msg } from "@/i18n";
import { Badge, type Tone } from "./ui";
import type { ApiTool, ToolCallStatus } from "@/lib/api";

/* API tools aren't in the design system yet: built from its tokens and rules (teal acts, status always has a word). */

const TOOL_STATUS: Record<ApiTool["status"], [Tone, string]> = {
  active: ["brand", "Active"],
  draft: ["neutral", "Draft"],
  disabled: ["warn", "Turned off"],
};

export function ToolStatusBadge({ status }: { status: ApiTool["status"] }) {
  const [tone, word] = TOOL_STATUS[status] ?? ["neutral", status];
  return (
    <Badge tone={tone} dot>
      {word}
    </Badge>
  );
}

export const CALL_WORD: Record<ToolCallStatus, string> = {
  ok: msg("Succeeded"),
  http_error: msg("API error"),
  timeout: msg("Timed out"),
  blocked: msg("Blocked"),
  rate_limited: msg("Rate limited"),
  invalid_args: msg("Invalid input"),
  too_large: msg("Response too large"),
  failed: msg("Failed"),
};

export function CallStatusBadge({ status, http }: { status: ToolCallStatus; http?: number | null }) {
  const word = CALL_WORD[status] ?? status;
  const tone: Tone = status === "ok" ? "brand" : status === "rate_limited" || status === "invalid_args" ? "warn" : "danger";
  return (
    <Badge tone={tone} dot>
      {status === "ok" ? word : t("Failed · {word}", { word: word })}
      {http ? ` · ${http}` : ""}
    </Badge>
  );
}

export const SOURCE_WORDS: Record<string, string> = { ai: msg("The chat (AI)"), visitor_email: msg("Visitor’s email"), visitor_name: msg("Visitor’s name") };
export const LOCATION_WORDS: Record<string, string> = { path: msg("Address path"), query: msg("Query string"), body: msg("JSON body") };

/** Placeholders in the URL's path, in order: https://shop.example.com/orders/{order_id} → ["order_id"]. */
export function placeholders(url: string): string[] {
  const path = url.replace(/^https?:\/\/[^/]*/i, "").split(/[?#]/)[0];
  return Array.from(path.matchAll(/\{([^{}]*)\}/g), (m) => m[1]);
}
