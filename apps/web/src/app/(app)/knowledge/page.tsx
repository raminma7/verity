"use client";

import { t as tr, tp } from "@/i18n";
import Link from "next/link";
import { useEffect, useMemo, useState } from "react";

import { DocumentRow } from "@/components/rag";
import { Banner, Button, EmptyState, Field, fmt, Icon, PageHeader, Segmented, Spinner, Stat } from "@/components/ui";
import { api, errorText, type KnowledgeStats, type Source } from "@/lib/api";
import { ACTIVE, docStatus, sourceSub } from "@/lib/knowledge";
import { limitText } from "@/lib/limits";
import { useSession } from "@/lib/session";
import { useData } from "@/lib/use-data";

function of(used: number, limit: number, noun: string) {
  if (limit < 0) return `Unlimited ${noun}`;
  const left = Math.max(0, limit - used);
  return left === 0 ? `Plan limit of ${fmt(limit)} reached` : `${fmt(left)} of ${fmt(limit)} left on your plan`;
}

export default function KnowledgeBase() {
  const { can } = useSession();
  const sources = useData<Source[]>("/knowledge/sources");
  const stats = useData<KnowledgeStats>("/knowledge/stats");
  const [filter, setFilter] = useState("all");
  const [q, setQ] = useState("");
  const [msg, setMsg] = useState<{ tone: "brand" | "danger"; text: string } | null>(null);
  const [menu, setMenu] = useState<string | null>(null);
  const manage = can("knowledge.manage");

  const busy = (sources.data ?? []).some((s) => ACTIVE.has(s.status));
  const { reload: reloadSources } = sources;
  const { reload: reloadStats } = stats;
  useEffect(() => {
    if (!busy) return;
    const t = setInterval(() => {
      reloadSources();
      reloadStats();
    }, 2500);
    return () => clearInterval(t);
  }, [busy, reloadSources, reloadStats]);

  const rows = useMemo(() => {
    const all = sources.data ?? [];
    const term = q.trim().toLowerCase();
    return all.filter(
      (s) =>
        (filter === "all" || docStatus(s.status) === filter || (filter === "indexing" && s.status === "queued")) &&
        (!term || s.name.toLowerCase().includes(term) || (s.root_url ?? "").toLowerCase().includes(term)),
    );
  }, [sources.data, filter, q]);

  async function act(fn: () => Promise<unknown>, ok: string) {
    setMenu(null);
    setMsg(null);
    try {
      await fn();
      setMsg({ tone: "brand", text: ok });
      await Promise.all([sources.reload(), stats.reload()]);
    } catch (e) {
      setMsg({ tone: "danger", text: errorText(e) });
    }
  }

  if (sources.loading && !sources.data) return <Spinner />;
  if (sources.error) return <Banner tone="danger">{sources.error.message}</Banner>;

  const st = stats.data;
  const count = (k: string) => (sources.data ?? []).filter((s) => docStatus(s.status) === k || (k === "indexing" && s.status === "queued")).length;

  return (
    <>
      <PageHeader
        title={tr("Knowledge base")}
        subtitle={tr("Everything your chatbot can answer from. Reprocess a document after you replace it; websites re-sync on their schedule.")}
        actions={
          manage ? (
            <Link className="vr-btn vr-btn-primary vr-btn-md" href="/knowledge/add">
              <Icon name="upload" />
              <span>{tr("Add knowledge")}</span>
            </Link>
          ) : null
        }
      />
      {msg ? <Banner tone={msg.tone}>{msg.text}</Banner> : null}

      {st ? (
        <div className="vr-grid vr-grid-4">
          <Stat label={tr("Documents")} value={fmt(st.usage.documents)} note={of(st.usage.documents, st.limits.documents, "documents")} />
          <Stat label={tr("Websites")} value={fmt(st.usage.domains)} note={of(st.usage.domains, st.limits.domains, "websites")} />
          <Stat label={tr("Crawled pages")} value={fmt(st.usage.crawled_pages)} note={of(st.usage.crawled_pages, st.limits.crawled_pages, "pages")} />
          <Stat
            label={tr("Storage")}
            value={`${(st.usage.storage_bytes / 1024 / 1024).toFixed(1)} MB`}
            note={`${st.limits.storage_mb < 0 ? tr("Unlimited") : tr("of {limit}", { limit: limitText("storage_mb", st.limits.storage_mb) })} · ${tp("{n} searchable chunk", "{n} searchable chunks", st.chunks)}`}
          />
        </div>
      ) : null}

      {(sources.data ?? []).length === 0 ? (
        <EmptyState
          icon="book"
          title={tr("Your knowledge base is empty")}
          action={
            manage ? (
              <Link className="vr-btn vr-btn-primary vr-btn-md" href="/knowledge/add">
                {tr("Add documents or a website")}
              </Link>
            ) : null
          }
        >
          {tr("Upload PDF, DOCX, TXT, CSV or XLSX files, or add your help-centre website. The chatbot answers only from what is here.")}
        </EmptyState>
      ) : (
        <>
          <div style={{ display: "flex", alignItems: "center", gap: 12, flexWrap: "wrap" }}>
            <div style={{ flex: "1 1 260px", maxWidth: 420 }}>
              <Field icon="search" placeholder={tr("Search by name or address…")} value={q} onChange={(e) => setQ(e.target.value)} aria-label={tr("Search sources")} />
            </div>
            <Segmented
              label={tr("Status")}
              value={filter}
              onChange={setFilter}
              options={[
                { value: "all", label: tr("All · {length}", { length: (sources.data ?? []).length }) },
                { value: "indexed", label: tr("Indexed · {count}", { count: count("indexed") }) },
                { value: "indexing", label: tr("Processing · {count}", { count: count("indexing") }) },
                { value: "failed", label: tr("Failed · {count}", { count: count("failed") }) },
              ]}
            />
          </div>
          <div className="vr-card" style={{ overflow: "visible" }} role="table" aria-label={tr("Knowledge sources")}>
            <div className="vr-docrow vr-docrow-head vr-eyebrow" role="row">
              <span />
              <span>{tr("Source")}</span>
              <span>{tr("Chunks")}</span>
              <span>{tr("Status")}</span>
              <span />
            </div>
            {rows.map((s) => (
              <DocumentRow
                key={s.id}
                name={s.name}
                type={s.kind === "website" ? "web" : s.file_type}
                sub={sourceSub(s)}
                chunks={s.chunk_count}
                status={docStatus(s.status)}
                href={`/knowledge/${s.id}`}
                actions={
                  manage ? (
                    <span style={{ position: "relative" }}>
                      <Button variant="ghost" size="sm" icon="dots" aria-label={tr("More actions for {name}", { name: s.name })} aria-expanded={menu === s.id} onClick={() => setMenu(menu === s.id ? null : s.id)} />
                      {menu === s.id ? (
                        <div className="vr-menu" role="menu" style={{ right: 0, left: "auto", top: "calc(100% + 4px)", minWidth: 180 }}>
                          <button role="menuitem" type="button" onClick={() => act(() => api.post(`/knowledge/sources/${s.id}/reprocess`), s.kind === "website" ? `Re-syncing ${s.name}.` : `Reprocessing ${s.name}.`)}>
                            <Icon name="refresh" /> {s.kind === "website" ? tr("Re-sync now") : tr("Reprocess")}
                          </button>
                          <Link role="menuitem" href={`/knowledge/${s.id}`}>
                            <Icon name="file" /> {tr("Open")}
                          </Link>
                          <hr />
                          <button
                            role="menuitem"
                            type="button"
                            style={{ color: "var(--danger)" }}
                            onClick={() => {
                              if (confirm(tr("Remove {name} from the knowledge base? The chatbot stops using it at once.", { name: s.name }))) act(() => api.del(`/knowledge/sources/${s.id}`), tr("{name} is being removed.", { name: s.name }));
                            }}
                          >
                            <Icon name="x" /> {tr("Remove")}
                          </button>
                        </div>
                      ) : null}
                    </span>
                  ) : (
                    <span />
                  )
                }
              />
            ))}
            {rows.length === 0 ? <p className="vr-muted" style={{ padding: 16, margin: 0 }}>{tr("No sources match.")}</p> : null}
          </div>
        </>
      )}
    </>
  );
}
