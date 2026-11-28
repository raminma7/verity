import { intlLocale, t, tp } from "@/i18n";
import type { DocStatus } from "@/components/rag";

import type { Source } from "./api";

export function docStatus(s: Source["status"]): DocStatus {
  return s === "ready" ? "indexed" : s === "processing" ? "indexing" : s === "queued" ? "queued" : "failed";
}

export function bytes(n: number) {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${Math.round(n / 1024)} KB`;
  return `${(n / 1024 / 1024).toFixed(1)} MB`;
}

export function ago(iso: string | null | undefined) {
  if (!iso) return t("never");
  const s = (Date.now() - new Date(iso).getTime()) / 1000;
  if (s < 60) return t("just now");
  if (s < 3600) return tp("{n} minute ago", "{n} minutes ago", Math.floor(s / 60));
  if (s < 86400) return tp("{n} hour ago", "{n} hours ago", Math.floor(s / 3600));
  const d = Math.floor(s / 86400);
  return d === 1 ? t("yesterday") : d < 30 ? tp("{n} day ago", "{n} days ago", d) : new Date(iso).toLocaleDateString(intlLocale(), { day: "numeric", month: "short" });
}

/** The DocumentRow sub-line: failures explain why, websites show their sync state. */
export function sourceSub(s: Source) {
  if (s.status === "failed" && s.error_message) return s.error_message;
  if (s.kind === "website") {
    const pages = tp("{n} page", "{n} pages", s.page_count ?? 0);
    const sync = s.sync_interval === "daily" ? t("syncs daily") : s.sync_interval === "weekly" ? t("syncs weekly") : t("manual sync");
    const parts = [s.root_url, pages, sync, t("synced {when}", { when: ago(s.last_synced_at) })];
    if (s.notice === "page_limit_reached") parts.push(t("page limit reached"));
    return parts.join(" · ");
  }
  return `${bytes(s.size_bytes ?? 0)} · ${s.status === "ready" ? t("processed {when}", { when: ago(s.processed_at) }) : t("uploaded {when}", { when: ago(s.created_at) })}`;
}

export const ACTIVE = new Set(["queued", "processing"]);
