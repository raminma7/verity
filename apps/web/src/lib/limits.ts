import { msg } from "@/i18n";
import { compact } from "@/components/ui";

import type { Limits } from "./api";

export const LIMIT_LABELS: Record<keyof Limits, string> = {
  documents: msg("Documents"),
  domains: msg("Crawled domains"),
  crawled_pages: msg("Crawled pages"),
  chatbot_domains: msg("Chatbot domains"),
  email_accounts: msg("Email accounts"),
  members: msg("Members"),
  storage_mb: msg("Storage"),
};

export const LIMIT_KEYS = Object.keys(LIMIT_LABELS) as (keyof Limits)[];

export function limitText(key: keyof Limits, v: number) {
  if (v < 0) return "Unlimited";
  if (key === "storage_mb") return v >= 1024 ? `${Math.round(v / 1024)} GB` : `${v} MB`;
  return compact(v);
}
