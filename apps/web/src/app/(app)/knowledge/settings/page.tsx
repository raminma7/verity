"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { PipelineTrace, SourceCard } from "@/components/rag";
import { Banner, Button, Card, EmptyState, PageHeader, Spinner } from "@/components/ui";
import { api, errorText, type TraceStep } from "@/lib/api";
import { useSession } from "@/lib/session";
import { useData } from "@/lib/use-data";

type Retrieval = { top_k: number; min_relevance: number; hybrid_weight: number };
type Result = { chunk_id: string; title: string; type: string; section: string; content: string; relevance: number; kept: boolean; vector_rank: number | null; keyword_rank: number | null };

function Slider({ label, help, value, min, max, step, onChange, format }: { label: string; help: string; value: number; min: number; max: number; step: number; onChange: (v: number) => void; format: (v: number) => string }) {
  return (
    <label className="vr-field">
      <span className="vr-field-top">
        {label}
        <span className="vr-mono">{format(value)}</span>
      </span>
      <input type="range" className="vr-slider" min={min} max={max} step={step} value={value} onChange={(e) => onChange(Number(e.target.value))} />
      <span className="vr-help">{help}</span>
    </label>
  );
}

export default function RetrievalSettings() {
  const { can } = useSession();
  const data = useData<{ retrieval: Retrieval; defaults: Retrieval }>("/knowledge/settings");
  const [cfg, setCfg] = useState<Retrieval | null>(null);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<{ tone: "brand" | "danger"; text: string } | null>(null);
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<{ results: Result[]; trace: TraceStep[] } | null>(null);
  const [testing, setTesting] = useState(false);
  useEffect(() => {
    if (data.data && !cfg) setCfg(data.data.retrieval);
  }, [data.data, cfg]);

  if (!data.data || !cfg) return data.error ? <Banner tone="danger">{data.error.message}</Banner> : <Spinner />;
  const manage = can("knowledge.manage");

  async function test(e: React.FormEvent) {
    e.preventDefault();
    if (!query.trim()) return;
    setTesting(true);
    try {
      setResults(await api.post("/knowledge/search", { query, settings: cfg }));
    } catch (e2) {
      setMsg({ tone: "danger", text: errorText(e2) });
    } finally {
      setTesting(false);
    }
  }

  return (
    <>
      <div>
        <Link className="vr-link" href="/knowledge" style={{ fontSize: 13 }}>
          ← Knowledge base
        </Link>
      </div>
      <PageHeader title="Retrieval settings" subtitle="How passages are found for every answer, in the chatbot and on Ask. Test a question to see what would be retrieved." />
      {msg ? <Banner tone={msg.tone}>{msg.text}</Banner> : null}
      <div className="vr-grid vr-grid-2" style={{ alignItems: "start" }}>
        <Card title="Retrieval">
          <div className="vr-form">
            <Slider label="Passages per answer (top-k)" help="More passages give fuller answers but cost more tokens." value={cfg.top_k} min={1} max={12} step={1} onChange={(v) => setCfg({ ...cfg, top_k: v })} format={(v) => String(v)} />
            <Slider
              label="Minimum relevance"
              help="Passages below this similarity are left out. If none is left, the chatbot says it couldn't find the answer instead of guessing."
              value={cfg.min_relevance}
              min={0}
              max={0.9}
              step={0.05}
              onChange={(v) => setCfg({ ...cfg, min_relevance: v })}
              format={(v) => v.toFixed(2)}
            />
            <Slider
              label="Hybrid weight"
              help="0 = keywords only, 1 = meaning only. Mixing both finds exact terms (product names, error codes) and paraphrases."
              value={cfg.hybrid_weight}
              min={0}
              max={1}
              step={0.1}
              onChange={(v) => setCfg({ ...cfg, hybrid_weight: v })}
              format={(v) => v.toFixed(1)}
            />
            {manage ? (
              <div className="vr-actions">
                <Button
                  variant="primary"
                  busy={busy}
                  onClick={async () => {
                    setBusy(true);
                    setMsg(null);
                    try {
                      await api.put("/knowledge/settings", cfg);
                      setMsg({ tone: "brand", text: "Retrieval settings saved." });
                    } catch (e) {
                      setMsg({ tone: "danger", text: errorText(e) });
                    } finally {
                      setBusy(false);
                    }
                  }}
                >
                  Save
                </Button>
                <Button variant="ghost" onClick={() => setCfg(data.data!.defaults)}>
                  Reset to defaults
                </Button>
              </div>
            ) : null}
          </div>
        </Card>
        <Card title="Test retrieval">
          <form onSubmit={test} style={{ display: "flex", gap: 8, marginBottom: 16 }}>
            <span className="vr-input" style={{ flex: 1 }}>
              <input placeholder="Ask a question your customers ask…" value={query} onChange={(e) => setQuery(e.target.value)} aria-label="Test question" />
            </span>
            <Button type="submit" icon="search" busy={testing} disabled={!query.trim()}>
              Test
            </Button>
          </form>
          {results ? (
            results.results.length ? (
              <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
                <PipelineTrace steps={results.trace} />
                {results.results.map((r, i) => (
                  <div key={r.chunk_id} style={{ opacity: r.kept ? 1 : 0.5 }}>
                    <SourceCard n={r.kept ? i + 1 : undefined} type={r.type} title={r.title} section={`${r.section ? `${r.section} · ` : ""}${r.kept ? "used" : "below cut-off"}`} score={r.relevance} excerpt={r.content.slice(0, 280)} />
                  </div>
                ))}
              </div>
            ) : (
              <EmptyState icon="search" title="Nothing found">
                No passage matches this question.
              </EmptyState>
            )
          ) : (
            <p className="vr-muted" style={{ margin: 0 }}>
              Runs retrieval only (one query embedding). Dimmed passages fall below the cut-off and would not be used.
            </p>
          )}
        </Card>
      </div>
    </>
  );
}
