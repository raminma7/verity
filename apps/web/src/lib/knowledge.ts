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
  if (!iso) return "never";
  const s = (Date.now() - new Date(iso).getTime()) / 1000;
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.floor(s / 60)} min ago`;
  if (s < 86400) return `${Math.floor(s / 3600)} h ago`;
  const d = Math.floor(s / 86400);
  return d === 1 ? "yesterday" : d < 30 ? `${d} days ago` : new Date(iso).toLocaleDateString("en-GB", { day: "numeric", month: "short" });
}

/** The DocumentRow sub-line: failures explain why, websites show their sync state. */
export function sourceSub(s: Source) {
  if (s.status === "failed" && s.error_message) return s.error_message;
  if (s.kind === "website") {
    const pages = `${s.page_count ?? 0} pages`;
    const sync = s.sync_interval === "off" ? "manual sync" : `syncs ${s.sync_interval}`;
    const note = s.notice === "page_limit_reached" ? " · page limit reached" : "";
    return `${s.root_url} · ${pages} · ${sync} · synced ${ago(s.last_synced_at)}${note}`;
  }
  return `${bytes(s.size_bytes ?? 0)} · ${s.status === "ready" ? `processed ${ago(s.processed_at)}` : `uploaded ${ago(s.created_at)}`}`;
}

export const ACTIVE = new Set(["queued", "processing"]);
