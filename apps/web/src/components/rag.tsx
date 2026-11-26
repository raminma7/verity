"use client";

/**
 * Verity design system: the Ask, Sources and Library components (QueryBar, AnswerCard, Citation,
 * SourceCard, RelevanceMeter, PipelineTrace, DocumentRow, FileTag), ported from the design system bundle
 * (window.Verity) to typed React. Class names match the bundle, so src/design/verity.css styles them
 * unchanged. Teal acts, lime marks evidence, status always carries a word.
 */
import { Fragment, useState, type ReactNode } from "react";

import { Badge, Button, cx, Icon } from "./ui";

export function FileTag({ type }: { type?: string | null }) {
  const t = String(type || "doc").toLowerCase();
  return <span className={cx("vr-filetag", `vr-filetag-${t === "web" ? "md" : t}`)}>{t === "web" ? "WEB" : t.toUpperCase().slice(0, 4)}</span>;
}

export function RelevanceMeter({ score, label = "Relevance" }: { score: number; label?: string }) {
  const s = Math.max(0, Math.min(1, Number(score) || 0));
  const tier = s >= 0.75 ? "high" : s >= 0.5 ? "mid" : "low";
  return (
    <span className={cx("vr-meter", `vr-meter-${tier}`)} role="meter" aria-valuemin={0} aria-valuemax={1} aria-valuenow={s} aria-label={label}>
      <span className="vr-meter-track">
        <span className="vr-meter-fill" style={{ width: `${Math.round(s * 100)}%` }} />
      </span>
      <span className="vr-meter-num">{s.toFixed(2)}</span>
    </span>
  );
}

export function Citation({ n, title, active, onClick }: { n: number; title?: string; active?: boolean; onClick?: () => void }) {
  return (
    <button type="button" className={cx("vr-cite", active && "is-active")} onClick={onClick} title={title} aria-label={`Source ${n}${title ? `: ${title}` : ""}`}>
      {n}
    </button>
  );
}

function markText(text: string, mark?: string | null): ReactNode {
  if (!mark || !text) return text;
  const i = text.toLowerCase().indexOf(mark.toLowerCase().slice(0, 200));
  if (i < 0) return text;
  const len = Math.min(mark.length, text.length - i);
  return (
    <>
      {text.slice(0, i)}
      <mark className="vr-mark">{text.slice(i, i + len)}</mark>
      {text.slice(i + len)}
    </>
  );
}

export function SourceCard({
  n,
  type,
  title,
  section,
  score,
  excerpt,
  highlight,
  active,
  href,
  onClick,
}: {
  n?: number;
  type?: string | null;
  title: string;
  section?: string | null;
  score?: number | null;
  excerpt?: string | null;
  highlight?: string | null;
  active?: boolean;
  href?: string | null;
  onClick?: () => void;
}) {
  return (
    <article className={cx("vr-source", active && "is-active")} onClick={onClick}>
      <header className="vr-source-head">
        {n != null ? <span className="vr-source-n">{n}</span> : null}
        <FileTag type={type} />
        <span className="vr-source-title">
          {href ? (
            <a className="vr-source-name" href={href} target="_blank" rel="noopener noreferrer" onClick={(e) => e.stopPropagation()}>
              {title}
            </a>
          ) : (
            <span className="vr-source-name">{title}</span>
          )}
          <span className="vr-source-sub">{section || " "}</span>
        </span>
        {score != null ? <RelevanceMeter score={score} /> : null}
      </header>
      {excerpt ? <p className="vr-source-excerpt">{markText(excerpt, highlight)}</p> : null}
    </article>
  );
}

export type Confidence = "high" | "medium" | "low" | "none" | null | undefined;

const CONF: Record<string, [tone: "brand" | "warn" | "danger" | "neutral", label: string]> = {
  high: ["brand", "High confidence"],
  medium: ["warn", "Partial evidence"],
  low: ["danger", "Low confidence"],
  none: ["danger", "No sources found"],
};

export function ConfidenceBadge({ confidence }: { confidence: Confidence }) {
  const c = CONF[confidence ?? "none"] ?? CONF.none;
  return (
    <Badge tone={c[0]} dot>
      {c[1]}
    </Badge>
  );
}

export function AnswerCard({
  confidence,
  question,
  sourceCount,
  model,
  latencyMs,
  children,
  onCopy,
  onRegenerate,
  label = "Answer",
}: {
  confidence: Confidence;
  question?: string;
  sourceCount?: number;
  model?: string | null;
  latencyMs?: number | null;
  children: ReactNode;
  onCopy?: () => void;
  onRegenerate?: () => void;
  label?: string;
}) {
  const meta = [sourceCount != null ? `${sourceCount} source${sourceCount === 1 ? "" : "s"}` : null, model, latencyMs != null ? `${(latencyMs / 1000).toFixed(1)}s` : null]
    .filter(Boolean)
    .join("  ·  ");
  return (
    <article className="vr-answer">
      {question ? <p className="vr-answer-q">{question}</p> : null}
      <header className="vr-answer-head">
        <span className="vr-eyebrow">
          <Icon name="spark" size={14} />
          {label}
        </span>
        {confidence !== undefined ? <ConfidenceBadge confidence={confidence} /> : null}
      </header>
      <div className="vr-answer-body">{children}</div>
      <footer className="vr-answer-foot">
        <span className="vr-answer-meta">{meta}</span>
        <span className="vr-answer-actions">
          {onCopy ? <Button variant="ghost" size="sm" icon="copy" aria-label="Copy answer" onClick={onCopy} /> : null}
          {onRegenerate ? <Button variant="ghost" size="sm" icon="refresh" aria-label="Ask again" onClick={onRegenerate} /> : null}
        </span>
      </footer>
    </article>
  );
}

export type AnswerBlock = { text: string; cites: number[] };

/** Answer text with each supported claim highlighted and followed by its Citation chips. */
export function CitedText({
  blocks,
  active,
  onCite,
  titles,
}: {
  blocks: AnswerBlock[];
  active?: number | null;
  onCite?: (n: number) => void;
  titles?: Record<number, string>;
}) {
  const paragraphs: AnswerBlock[][] = [[]];
  for (const b of blocks) {
    const parts = b.text.split(/\n{2,}/);
    parts.forEach((p, i) => {
      if (i > 0) paragraphs.push([]);
      paragraphs[paragraphs.length - 1].push({ text: p, cites: i === parts.length - 1 ? b.cites : [] });
    });
  }
  return (
    <>
      {paragraphs
        .filter((p) => p.some((b) => b.text.trim() || b.cites.length))
        .map((p, i) => (
          <p key={i} style={{ whiteSpace: "pre-line" }}>
            {p.map((b, j) => (
              <Fragment key={j}>
                {b.cites.length ? <span className="vr-cited">{b.text.trimEnd()}</span> : b.text}
                {b.cites.map((n) => (
                  <Citation key={n} n={n} title={titles?.[n]} active={active === n} onClick={() => onCite?.(n)} />
                ))}
                {b.cites.length && /\s$/.test(b.text) ? " " : null}
              </Fragment>
            ))}
          </p>
        ))}
    </>
  );
}

export function QueryBar({
  placeholder = "Ask a question about your documents…",
  hint = "⏎ to ask",
  scopes,
  rows = 2,
  busy,
  onSubmit,
  autoFocus,
}: {
  placeholder?: string;
  hint?: string;
  scopes?: { label: string; active?: boolean; onClick?: () => void }[];
  rows?: number;
  busy?: boolean;
  onSubmit: (q: string) => void;
  autoFocus?: boolean;
}) {
  const [v, setV] = useState("");
  function submit(e?: { preventDefault: () => void }) {
    e?.preventDefault();
    const q = v.trim();
    if (!q || busy) return;
    onSubmit(q);
    setV("");
  }
  return (
    <form className={cx("vr-query", busy && "is-busy")} onSubmit={submit} role="search">
      <textarea
        className="vr-query-input"
        rows={rows}
        placeholder={placeholder}
        value={v}
        aria-label="Question"
        autoFocus={autoFocus}
        onChange={(e) => setV(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter" && !e.shiftKey) submit(e);
        }}
      />
      <div className="vr-query-bar">
        <div className="vr-query-scopes">
          {(scopes ?? []).map((s) => (
            <button key={s.label} type="button" className={cx("vr-chip", s.active && "is-active")} aria-pressed={!!s.active} onClick={s.onClick}>
              {s.label}
            </button>
          ))}
        </div>
        <div className="vr-query-meta">
          {hint ? <span className="vr-query-hint">{hint}</span> : null}
          <button type="submit" className="vr-send" disabled={busy || !v.trim()} aria-label={busy ? "Retrieving" : "Ask"}>
            {busy ? <span className="vr-spin" aria-hidden="true" /> : <Icon name="send" size={18} />}
          </button>
        </div>
      </div>
    </form>
  );
}

export type TraceStep = { name: string; detail?: string; ms?: number | null; state?: "done" | "running" | "pending" | "error" };

export function PipelineTrace({ steps }: { steps: TraceStep[] }) {
  return (
    <ol className="vr-trace">
      {steps.map((s, i) => {
        const state = s.state || "done";
        return (
          <li key={i} className={cx("vr-trace-step", `is-${state}`)}>
            <span className="vr-trace-node" aria-hidden="true">
              {state === "done" ? <Icon name="check" size={12} /> : state === "error" ? <Icon name="x" size={12} /> : state === "running" ? <span className="vr-spin vr-spin-sm" /> : null}
            </span>
            <span className="vr-trace-text">
              <span className="vr-trace-name">{s.name}</span>
              {s.detail ? <span className="vr-trace-detail">{s.detail}</span> : null}
            </span>
            <span className="vr-trace-ms">{s.ms != null ? `${s.ms} ms` : state === "running" ? "…" : ""}</span>
          </li>
        );
      })}
    </ol>
  );
}

export type DocStatus = "indexed" | "indexing" | "queued" | "failed";

const STATUS: Record<DocStatus, ["brand" | "warn" | "neutral" | "danger", string]> = {
  indexed: ["brand", "Indexed"],
  indexing: ["warn", "Indexing"],
  queued: ["neutral", "Queued"],
  failed: ["danger", "Failed"],
};

export function StatusBadge({ status }: { status: DocStatus }) {
  const s = STATUS[status];
  return (
    <Badge tone={s[0]} dot>
      {s[1]}
    </Badge>
  );
}

export function DocumentRow({
  name,
  type,
  sub,
  chunks,
  status,
  href,
  actions,
}: {
  name: string;
  type?: string | null;
  sub?: ReactNode;
  chunks?: number | null;
  status: DocStatus;
  href?: string;
  actions?: ReactNode;
}) {
  return (
    <div className="vr-docrow" role="row">
      <FileTag type={type} />
      <span className="vr-docrow-main">
        {href ? (
          <a className="vr-docrow-name" href={href}>
            {name}
          </a>
        ) : (
          <span className="vr-docrow-name">{name}</span>
        )}
        <span className="vr-docrow-sub">{sub}</span>
      </span>
      <span className="vr-docrow-chunks">{chunks ? `${chunks} chunk${chunks === 1 ? "" : "s"}` : "—"}</span>
      <span className="vr-docrow-status">
        <StatusBadge status={status} />
        {status === "indexing" ? (
          <span className="vr-docrow-progress vr-docrow-progress-indeterminate">
            <span style={{ width: "40%" }} />
          </span>
        ) : null}
      </span>
      {actions ?? <span />}
    </div>
  );
}
