"use client";

import { t as tr } from "@/i18n";
import Link from "next/link";
import { useEffect, useRef, useState } from "react";

import { FileTag, StatusBadge } from "@/components/rag";
import { Banner, Button, Card, Field, PageHeader, Segmented, Select, fmt } from "@/components/ui";
import { api, errorText, type KnowledgeStats, type Source } from "@/lib/api";
import { ACTIVE, bytes, docStatus } from "@/lib/knowledge";
import { useSession } from "@/lib/session";
import { useData } from "@/lib/use-data";

const ACCEPT = ".pdf,.docx,.txt,.csv,.xlsx";

type Item = { key: string; name: string; size: number; type: string; error?: string; source?: Source };

export default function AddKnowledge() {
  const { can } = useSession();
  const stats = useData<KnowledgeStats>("/knowledge/stats");
  const [tab, setTab] = useState("files");
  const [items, setItems] = useState<Item[]>([]);
  const [drag, setDrag] = useState(false);
  const input = useRef<HTMLInputElement>(null);

  // Refresh the processing state of what was uploaded here until it settles.
  const pending = items.filter((i) => i.source && ACTIVE.has(i.source.status)).map((i) => i.source!.id);
  const pendingKey = pending.join(",");
  useEffect(() => {
    if (!pendingKey) return;
    const t = setInterval(async () => {
      const ids = pendingKey.split(",");
      const fresh = await Promise.all(ids.map((id) => api.get<Source>(`/knowledge/sources/${id}`).catch(() => null)));
      setItems((prev) => prev.map((i) => ({ ...i, source: fresh.find((f) => f && f.id === i.source?.id) ?? i.source })));
    }, 2000);
    return () => clearInterval(t);
  }, [pendingKey]);

  async function add(files: FileList | File[]) {
    const list = Array.from(files);
    const max = (stats.data?.max_upload_mb ?? 25) * 1024 * 1024;
    const fresh: Item[] = list.map((f, i) => ({ key: `${Date.now()}-${i}-${f.name}`, name: f.name, size: f.size, type: f.name.split(".").pop() ?? "" }));
    setItems((prev) => [...fresh, ...prev]);
    await Promise.all(
      list.map(async (f, i) => {
        const key = fresh[i].key;
        let patch: Partial<Item>;
        if (f.size > max) patch = { error: `Larger than ${stats.data?.max_upload_mb ?? 25} MB.` };
        else {
          try {
            patch = { source: await api.upload<Source>("/knowledge/files", f) };
          } catch (e) {
            patch = { error: errorText(e) };
          }
        }
        setItems((prev) => prev.map((it) => (it.key === key ? { ...it, ...patch } : it)));
      }),
    );
    stats.reload();
  }

  if (!can("knowledge.manage")) return <Banner tone="warn">{tr("Your role can browse the knowledge base but not add to it.")}</Banner>;
  const done = items.filter((i) => i.source?.status === "ready").length;

  return (
    <>
      <div>
        <Link className="vr-link" href="/knowledge" style={{ fontSize: 13 }}>
          {tr("← Knowledge base")}
        </Link>
      </div>
      <PageHeader title={tr("Add knowledge")} subtitle={tr("Documents and websites are parsed, split into passages and indexed for search. Indexing uses AI tokens for embeddings.")} />
      <Segmented
        label={tr("Source type")}
        value={tab}
        onChange={setTab}
        options={[
          { value: "files", label: tr("Upload documents") },
          { value: "website", label: tr("Crawl a website") },
        ]}
      />
      {tab === "files" ? (
        <>
          <label
            className={`vr-drop${drag ? " is-over" : ""}`}
            onDragOver={(e) => {
              e.preventDefault();
              setDrag(true);
            }}
            onDragLeave={() => setDrag(false)}
            onDrop={(e) => {
              e.preventDefault();
              setDrag(false);
              if (e.dataTransfer.files.length) add(e.dataTransfer.files);
            }}
          >
            <span className="vr-drop-tile" aria-hidden="true">
              <svg className="vr-icon" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
                <path d="M12 16V4M6 10l6-6 6 6M4 20h16" />
              </svg>
            </span>
            <span style={{ display: "flex", flexDirection: "column", gap: 4 }}>
              <span className="vr-display" style={{ fontSize: 28, lineHeight: "34px" }}>
                {tr("Drop files here")}
              </span>
              <span className="vr-muted">{tr("PDF, DOCX, TXT, CSV or XLSX · up to")} {stats.data?.max_upload_mb ?? 25} {tr("MB each")}</span>
            </span>
            <span className="vr-btn vr-btn-secondary vr-btn-md" role="button">
              <span>{tr("Browse files")}</span>
            </span>
            <input
              ref={input}
              type="file"
              accept={ACCEPT}
              multiple
              hidden
              onChange={(e) => {
                if (e.target.files?.length) add(e.target.files);
                e.target.value = "";
              }}
            />
          </label>
          {items.length ? (
            <>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline" }}>
                <h2 className="vr-h">{tr("Processing")} {items.length} {tr("file")}{items.length === 1 ? "" : "s"}</h2>
                <span className="vr-mono vr-muted" style={{ fontSize: 12 }}>
                  {tr("{done} of {total} done", { done, total: items.length })}
                </span>
              </div>
              <div className="vr-card vr-list" style={{ overflow: "hidden" }}>
                {items.map((i) => (
                  <QueueRow key={i.key} item={i} />
                ))}
              </div>
            </>
          ) : null}
        </>
      ) : (
        <WebsiteForm stats={stats.data} onAdded={() => stats.reload()} />
      )}
    </>
  );
}

function QueueRow({ item }: { item: Item }) {
  const s = item.source;
  const status = item.error ? "failed" : s ? docStatus(s.status) : "queued";
  const pct = status === "indexed" || status === "failed" ? 100 : status === "indexing" ? 60 : s ? 15 : 5;
  const tone = status === "failed" ? "var(--danger)" : status === "indexed" ? "var(--brand)" : "var(--warn)";
  const note = item.error
    ? item.error
    : !s
      ? "Uploading…"
      : s.status === "ready"
        ? `Done · ${s.chunk_count} chunks`
        : s.status === "failed"
          ? s.error_message
          : s.status === "processing"
            ? "Parsing, chunking and embedding…"
            : "Waiting for a worker…";
  return (
    <div style={{ gap: 14 }}>
      <FileTag type={item.type} />
      <span style={{ flex: 1, minWidth: 0, display: "flex", flexDirection: "column", gap: 6 }}>
        <span style={{ display: "flex", justifyContent: "space-between", gap: 12 }}>
          <span style={{ fontWeight: 500, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
            {s ? <Link href={`/knowledge/${s.id}`}>{item.name}</Link> : item.name}
          </span>
          <span className="vr-mono vr-muted" style={{ fontSize: 12, textAlign: "right" }}>
            {bytes(item.size)}
          </span>
        </span>
        <span className="vr-progress" aria-hidden="true">
          <span style={{ width: `${pct}%`, background: tone }} />
        </span>
        <span className="vr-help" style={status === "failed" ? { color: "var(--danger)" } : undefined}>
          {note}
        </span>
      </span>
      <StatusBadge status={status} />
    </div>
  );
}

function WebsiteForm({ stats, onAdded }: { stats: KnowledgeStats | null; onAdded: () => void }) {
  const [url, setUrl] = useState("");
  const [depth, setDepth] = useState("3");
  const [include, setInclude] = useState("");
  const [exclude, setExclude] = useState("");
  const [sync, setSync] = useState("weekly");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [added, setAdded] = useState<Source | null>(null);
  const full = stats && stats.limits.domains >= 0 && stats.usage.domains >= stats.limits.domains;
  const lines = (s: string) => s.split(/[\n,]/).map((x) => x.trim()).filter(Boolean);

  return (
    <div className="vr-grid vr-grid-2" style={{ alignItems: "start" }}>
      <Card title={tr("Website")}>
        <form
          className="vr-form"
          onSubmit={async (e) => {
            e.preventDefault();
            setBusy(true);
            setErr(null);
            try {
              const s = await api.post<Source>("/knowledge/websites", {
                url,
                max_depth: Number(depth),
                include_paths: lines(include),
                exclude_paths: lines(exclude),
                sync_interval: sync,
              });
              setAdded(s);
              setUrl("");
              onAdded();
            } catch (e2) {
              setErr(errorText(e2));
            } finally {
              setBusy(false);
            }
          }}
        >
          <Field label={tr("Start address")} placeholder="https://help.example.com" value={url} onChange={(e) => setUrl(e.target.value)} required error={err} />
          <div className="vr-form-row" style={{ gridTemplateColumns: "1fr 1fr" }}>
            <Select
              label={tr("Follow links")}
              value={depth}
              onChange={setDepth}
              options={[
                { value: "0", label: tr("This page only") },
                { value: "1", label: tr("1 level deep") },
                { value: "2", label: tr("2 levels deep") },
                { value: "3", label: tr("3 levels deep") },
                { value: "5", label: tr("5 levels deep") },
              ]}
            />
            <Select
              label={tr("Re-sync")}
              value={sync}
              onChange={setSync}
              options={[
                { value: "daily", label: tr("Every day") },
                { value: "weekly", label: tr("Every week") },
                { value: "off", label: tr("Only when I ask") },
              ]}
            />
          </div>
          <Field label={tr("Only these paths")} placeholder="/docs, /help" help={tr("Optional. Comma-separated path prefixes.")} value={include} onChange={(e) => setInclude(e.target.value)} />
          <Field label={tr("Skip these paths")} placeholder="/blog, /careers" value={exclude} onChange={(e) => setExclude(e.target.value)} />
          <Button type="submit" variant="primary" icon="link" busy={busy} disabled={!url || !!full}>
            {tr("Start crawl")}
          </Button>
          {full ? <p className="vr-help">{tr("Your plan’s website limit is reached. Upgrade or remove a website to add another.")}</p> : null}
        </form>
      </Card>
      <Card title={tr("How crawling works")}>
        <ul className="vr-muted" style={{ margin: 0, paddingLeft: 18, display: "flex", flexDirection: "column", gap: 8 }}>
          <li>{tr("Verity starts at the address and its sitemap, follows links on the same site and respects robots.txt.")}</li>
          <li>{tr("Only the main text of each page is kept; navigation, footers and cookie banners are left out.")}</li>
          <li>{tr("On re-sync, only pages that changed are embedded again, so re-syncs cost few tokens.")}</li>
          <li>
            {tr("Your plan allows {limit} crawled pages in total; {used} are used.", { limit: stats ? (stats.limits.crawled_pages < 0 ? tr("unlimited") : fmt(stats.limits.crawled_pages)) : "…", used: stats ? fmt(stats.usage.crawled_pages) : "…" })}
          </li>
        </ul>
        {added ? (
          <div style={{ marginTop: 16 }}>
            <Banner tone="brand" title={tr("Crawling {domain}", { domain: added.domain })} action={<Link className="vr-btn vr-btn-secondary vr-btn-sm" href={`/knowledge/${added.id}`}>{tr("Follow progress")}</Link>}>
              {tr("Pages appear in the knowledge base as they’re indexed.")}
            </Banner>
          </div>
        ) : null}
      </Card>
    </div>
  );
}
