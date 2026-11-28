"use client";

import { t as tr, msg, tp } from "@/i18n";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { FileTag, PipelineTrace, StatusBadge } from "@/components/rag";
import { Badge, Banner, Button, Card, dateTime, EmptyState, Field, fmt, PageHeader, Segmented, Select, Spinner } from "@/components/ui";
import { api, errorText, type Source } from "@/lib/api";
import { ACTIVE, ago, bytes, docStatus } from "@/lib/knowledge";
import { useSession } from "@/lib/session";
import { useData } from "@/lib/use-data";

type Chunk = { id: string; ordinal: number; content: string; token_count: number; meta: Record<string, string | number>; cited: number };
type Page = { id: string; url: string; title: string; status: string; error_code: string | null; http_status: number | null; chunk_count: number; fetched_at: string | null };

const PAGE_ERRORS: Record<string, string> = {
  noindex: msg("Marked noindex"),
  not_html: msg("Not a web page"),
  no_content: msg("No readable text"),
  too_large: msg("Too large"),
  blocked_address: msg("Private address"),
  timeout: msg("Timed out"),
  off_site_redirect: msg("Redirects off the site"),
};

function where(meta: Chunk["meta"]) {
  if (meta.page) return `Page ${meta.page}${meta.page_end ? `–${meta.page_end}` : ""}`;
  if (meta.sheet) return `${meta.sheet} · rows ${meta.rows}`;
  if (meta.rows) return `Rows ${meta.rows}`;
  if (meta.url) return String(meta.url).replace(/^https?:\/\//, "");
  return meta.heading ? String(meta.heading) : "";
}

export default function SourcePage() {
  const { id } = useParams<{ id: string }>();
  const router = useRouter();
  const { can } = useSession();
  const src = useData<Source>(`/knowledge/sources/${id}`);
  const [offset, setOffset] = useState(0);
  const chunks = useData<{ total: number; items: Chunk[] }>(`/knowledge/sources/${id}/chunks?offset=${offset}&limit=50`);
  const pages = useData<Page[]>(src.data?.kind === "website" ? `/knowledge/sources/${id}/pages` : null);
  const [tab, setTab] = useState("passages");
  const [selected, setSelected] = useState<string | null>(null);
  const [msg, setMsg] = useState<{ tone: "brand" | "danger"; text: string } | null>(null);
  const manage = can("knowledge.manage");

  const active = src.data ? ACTIVE.has(src.data.status) : false;
  const { reload: reloadSrc } = src;
  const { reload: reloadChunks } = chunks;
  useEffect(() => {
    if (!active) return;
    const t = setInterval(() => {
      reloadSrc();
      reloadChunks();
    }, 2000);
    return () => clearInterval(t);
  }, [active, reloadSrc, reloadChunks]);

  async function act(fn: () => Promise<unknown>, ok: string) {
    setMsg(null);
    try {
      await fn();
      setMsg({ tone: "brand", text: ok });
      await src.reload();
    } catch (e) {
      setMsg({ tone: "danger", text: errorText(e) });
    }
  }

  if (src.loading && !src.data) return <Spinner />;
  if (src.error || !src.data) return <Banner tone="danger">{src.error?.message ?? tr("Not found.")}</Banner>;
  const s = src.data;
  const web = s.kind === "website";
  const sel = chunks.data?.items.find((c) => c.id === selected) ?? null;

  return (
    <>
      <div>
        <Link className="vr-link" href="/knowledge" style={{ fontSize: 13 }}>
          {tr("← Knowledge base")}
        </Link>
      </div>
      <PageHeader
        title={s.name}
        subtitle={
          <span style={{ display: "inline-flex", gap: 10, alignItems: "center", flexWrap: "wrap" }}>
            <FileTag type={web ? "web" : s.file_type} />
            <StatusBadge status={docStatus(s.status)} />
            <span className="vr-mono" style={{ fontSize: 12 }}>
              {web ? tr("{root_url} · {fmt} pages", { root_url: s.root_url, fmt: fmt(s.page_count ?? 0) }) : bytes(s.size_bytes ?? 0)} · {tr("{chunks} chunks · {tokens} tokens", { chunks: fmt(s.chunk_count), tokens: fmt(s.token_count) })}
            </span>
          </span>
        }
        actions={
          manage ? (
            <>
              <Button icon="refresh" disabled={active} onClick={() => act(() => api.post(`/knowledge/sources/${s.id}/reprocess`), web ? "Re-sync started." : "Reprocessing started.")}>
                {web ? tr("Re-sync now") : tr("Reprocess")}
              </Button>
              <Button
                variant="danger"
                icon="x"
                onClick={async () => {
                  if (!confirm(tr("Remove {name}? The chatbot stops using it at once.", { name: s.name }))) return;
                  try {
                    await api.del(`/knowledge/sources/${s.id}`);
                    router.push("/knowledge");
                  } catch (e) {
                    setMsg({ tone: "danger", text: errorText(e) });
                  }
                }}
              >
                {tr("Remove")}
              </Button>
            </>
          ) : null
        }
      />
      {msg ? <Banner tone={msg.tone}>{msg.text}</Banner> : null}
      {s.status === "failed" ? <Banner tone="danger" title={tr("Processing failed")}>{s.error_message}</Banner> : null}
      {s.notice === "page_limit_reached" ? <Banner tone="warn" title={tr("Page limit reached")}>{tr("Some pages weren’t indexed because your plan’s crawled-page limit is reached.")}</Banner> : null}

      <div className="vr-doc-layout">
        <section style={{ minWidth: 0, display: "flex", flexDirection: "column", gap: 16 }}>
          {web ? (
            <Segmented
              label={tr("View")}
              value={tab}
              onChange={setTab}
              options={[
                { value: "passages", label: tr("Passages · {fmt}", { fmt: fmt(chunks.data?.total ?? s.chunk_count) }) },
                { value: "pages", label: tr("Pages · {fmt}", { fmt: fmt(pages.data?.length ?? s.page_count ?? 0) }) },
              ]}
            />
          ) : null}
          {tab === "pages" && web ? (
            <div className="vr-card vr-table-wrap">
              <table className="vr-table">
                <thead>
                  <tr>
                    <th>{tr("Page")}</th>
                    <th>{tr("Chunks")}</th>
                    <th>{tr("Status")}</th>
                    <th>{tr("Fetched")}</th>
                  </tr>
                </thead>
                <tbody>
                  {(pages.data ?? []).map((p) => (
                    <tr key={p.id}>
                      <td style={{ maxWidth: 420 }}>
                        <div style={{ fontWeight: 500, overflow: "hidden", textOverflow: "ellipsis" }}>{p.title || p.url}</div>
                        <a className="vr-mono vr-muted" href={p.url} target="_blank" rel="noopener noreferrer" style={{ fontSize: 12, wordBreak: "break-all" }}>
                          {p.url}
                        </a>
                      </td>
                      <td className="vr-mono">{p.chunk_count || "—"}</td>
                      <td>
                        {p.status === "ready" ? (
                          <Badge tone="brand" dot>
                            {tr("Indexed")}
                          </Badge>
                        ) : (
                          <Badge tone={p.status === "skipped" ? "neutral" : "danger"} dot>
                            {tr(PAGE_ERRORS[p.error_code ?? ""]) ?? (p.http_status ? tr("HTTP {http_status}", { http_status: p.http_status }) : tr("Failed"))}
                          </Badge>
                        )}
                      </td>
                      <td className="vr-muted">{ago(p.fetched_at)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : chunks.data && chunks.data.items.length ? (
            <div className="vr-paper">
              {chunks.data.items.map((c) => (
                <button key={c.id} type="button" className={`vr-chunk${selected === c.id ? " is-on" : ""}`} onClick={() => setSelected(selected === c.id ? null : c.id)}>
                  <span className="vr-chunk-id vr-mono">
                    #{c.ordinal + 1} · {where(c.meta)}
                  </span>
                  <span className="vr-chunk-text">{c.content}</span>
                </button>
              ))}
              {chunks.data.total > 50 ? (
                <div style={{ display: "flex", gap: 8, justifyContent: "center", paddingTop: 8 }}>
                  <Button size="sm" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - 50))}>
                    {tr("Previous")}
                  </Button>
                  <span className="vr-muted vr-mono" style={{ fontSize: 12, alignSelf: "center" }}>
                    {tr("{from}–{to} of {total}", { from: offset + 1, to: Math.min(offset + 50, chunks.data.total), total: chunks.data.total })}
                  </span>
                  <Button size="sm" disabled={offset + 50 >= chunks.data.total} onClick={() => setOffset(offset + 50)}>
                    {tr("Next")}
                  </Button>
                </div>
              ) : null}
            </div>
          ) : (
            <EmptyState icon="file" title={active ? tr("Processing…") : tr("No passages yet")}>
              {active ? tr("Passages appear here once the document is indexed.") : tr("Reprocess to try again.")}
            </EmptyState>
          )}
        </section>

        <aside style={{ display: "flex", flexDirection: "column", gap: 16 }}>
          {sel ? (
            <Card title={tr("Selected passage")}>
              <dl className="vr-kv">
                <dt>{tr("Location")}</dt>
                <dd>{where(sel.meta) || "—"}</dd>
                <dt>{tr("Tokens")}</dt>
                <dd className="vr-mono">{sel.token_count}</dd>
                <dt>{tr("Cited in answers")}</dt>
                <dd className="vr-mono">{sel.cited}</dd>
                <dt>{tr("Chunk ID")}</dt>
                <dd className="vr-mono" style={{ fontSize: 11, wordBreak: "break-all" }}>
                  {sel.id}
                </dd>
              </dl>
            </Card>
          ) : null}
          <Card title={tr("Last run")}>
            {s.steps.length ? <PipelineTrace steps={active ? [...s.steps, { name: "Working", state: "running" }] : s.steps} /> : <p className="vr-muted" style={{ margin: 0 }}>{active ? tr("Waiting for a worker…") : tr("Not processed yet.")}</p>}
            <p className="vr-help" style={{ marginTop: 12 }}>
              {s.processed_at ? tr("Finished {processed_at}", { processed_at: dateTime(s.processed_at) }) : tr("Added {created_at}", { created_at: dateTime(s.created_at) })}
            </p>
          </Card>
          {web && manage ? <WebsiteSettings source={s} onSaved={src.reload} /> : null}
        </aside>
      </div>
    </>
  );
}

function WebsiteSettings({ source, onSaved }: { source: Source; onSaved: () => void }) {
  const [sync, setSync] = useState(source.sync_interval ?? "weekly");
  const [depth, setDepth] = useState(String(source.max_depth ?? 3));
  const [include, setInclude] = useState((source.include_paths ?? []).join(", "));
  const [exclude, setExclude] = useState((source.exclude_paths ?? []).join(", "));
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const lines = (v: string) => v.split(/[\n,]/).map((x) => x.trim()).filter(Boolean);
  return (
    <Card title={tr("Crawl settings")}>
      <form
        className="vr-form"
        onSubmit={async (e) => {
          e.preventDefault();
          setBusy(true);
          setMsg(null);
          try {
            await api.patch(`/knowledge/sources/${source.id}`, { sync_interval: sync, max_depth: Number(depth), include_paths: lines(include), exclude_paths: lines(exclude) });
            setMsg("Saved. Changes apply from the next sync.");
            onSaved();
          } catch (e2) {
            setMsg(errorText(e2));
          } finally {
            setBusy(false);
          }
        }}
      >
        <Select
          label={tr("Re-sync")}
          value={sync}
          onChange={(v) => setSync(v as typeof sync)}
          options={[
            { value: "daily", label: tr("Every day") },
            { value: "weekly", label: tr("Every week") },
            { value: "off", label: tr("Only when I ask") },
          ]}
        />
        <Select
          label={tr("Follow links")}
          value={depth}
          onChange={setDepth}
          options={["0", "1", "2", "3", "5"].map((d) => ({ value: d, label: d === "0" ? tr("Start page only") : tp("{n} level deep", "{n} levels deep", Number(d)) }))}
        />
        <Field label={tr("Only these paths")} value={include} onChange={(e) => setInclude(e.target.value)} placeholder="/docs" />
        <Field label={tr("Skip these paths")} value={exclude} onChange={(e) => setExclude(e.target.value)} placeholder="/blog" />
        <p className="vr-help" style={{ margin: 0 }}>
          {tr("Next sync:")} {source.next_sync_at ? dateTime(source.next_sync_at) : tr("not scheduled")}
        </p>
        <Button type="submit" busy={busy}>
          {tr("Save")}
        </Button>
        {msg ? <span className="vr-help">{msg}</span> : null}
      </form>
    </Card>
  );
}
