"use client";

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

const CALL_WORD: Record<ToolCallStatus, string> = {
  ok: "Succeeded",
  http_error: "API error",
  timeout: "Timed out",
  blocked: "Blocked",
  rate_limited: "Rate limited",
  invalid_args: "Invalid input",
  too_large: "Response too large",
  failed: "Failed",
};

export function CallStatusBadge({ status, http }: { status: ToolCallStatus; http?: number | null }) {
  const word = CALL_WORD[status] ?? status;
  const tone: Tone = status === "ok" ? "brand" : status === "rate_limited" || status === "invalid_args" ? "warn" : "danger";
  return (
    <Badge tone={tone} dot>
      {status === "ok" ? word : `Failed · ${word}`}
      {http ? ` · ${http}` : ""}
    </Badge>
  );
}

export const SOURCE_WORDS: Record<string, string> = { ai: "The AI, from the chat", visitor_email: "The visitor’s email", visitor_name: "The visitor’s name" };
export const LOCATION_WORDS: Record<string, string> = { path: "In the address", query: "Query string", body: "JSON body" };

/** Placeholders in the URL's path, in order: https://shop.example.com/orders/{order_id} → ["order_id"]. */
export function placeholders(url: string): string[] {
  const path = url.replace(/^https?:\/\/[^/]*/i, "").split(/[?#]/)[0];
  return Array.from(path.matchAll(/\{([^{}]*)\}/g), (m) => m[1]);
}
