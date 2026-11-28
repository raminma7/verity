"use client";

import { intlLocale, t } from "@/i18n";
/* Charts for Phase 5B (analytics, admin reports). The design system has no charts yet, so these are built only
   from its tokens and rules:
   - one hue: a teal ramp derived from --brand (--chart-1..3, validated for both themes in app.css), ordered by
     meaning, never cycled; status series use the reserved --danger / --warn and always carry a word;
   - thin marks with 2px surface gaps, rounded data ends on the baseline, recessive grid and axis;
   - a legend whenever there are two or more series, a tooltip on hover and focus, and a table view of every chart;
   - one axis per chart: measures of different scale get their own chart.
   No chart library: plain HTML/SVG, so nothing new ships at runtime. */

import { type ReactNode, useId, useMemo, useState } from "react";

import { cx, fmt, Segmented } from "@/components/ui";

export type Tone = "s1" | "s2" | "s3" | "danger" | "warn" | "muted";
export type Series = { key: string; label: string; tone: Tone };
export type Point = { date: string; [key: string]: number | string | null };

const TONE_VAR: Record<Tone, string> = {
  s1: "var(--chart-1)",
  s2: "var(--chart-2)",
  s3: "var(--chart-3)",
  danger: "var(--danger)",
  warn: "var(--warn)",
  muted: "var(--line-strong)",
};


/** "3 Nov" for a local calendar day (YYYY-MM-DD, already in the viewer's time zone), "Week of 3 Nov" for weeks. */
export function dayLabel(iso: string, bucket: "day" | "week" = "day", long = false) {
  const [y, m, d] = iso.split("-").map(Number);
  const s = new Date(Date.UTC(y, m - 1, d)).toLocaleDateString(intlLocale(), {
    day: "numeric",
    month: "short",
    ...(long ? { year: "numeric" } : {}),
    timeZone: "UTC",
  });
  return bucket === "week" ? t("Week of {date}", { date: s }) : s;
}

/** A max whose half is a whole number too, so a count axis never shows "0.5". */
function niceCountMax(v: number) {
  if (v <= 2) return 2;
  const p = 10 ** Math.floor(Math.log10(v));
  const steps = p === 1 ? [2, 4, 6, 8, 10] : [1, 2, 4, 5, 10];
  for (const m of steps) if (m * p >= v) return m * p;
  return 10 * p;
}

function niceMax(v: number) {
  if (v <= 0) return 1;
  const p = 10 ** Math.floor(Math.log10(v));
  for (const m of [1, 2, 2.5, 5, 10]) if (m * p >= v) return m * p;
  return 10 * p;
}

export function Legend({ series }: { series: Series[] }) {
  if (series.length < 2) return null;
  return (
    <ul className="vr-legend">
      {series.map((s) => (
        <li key={s.key}>
          <span className="vr-legend-key" style={{ background: TONE_VAR[s.tone] }} aria-hidden="true" />
          {t(s.label)}
        </li>
      ))}
    </ul>
  );
}

type Fmt = (n: number) => string;

/** Bars per bucket; two or more series stack, in the order given (the first sits on the baseline). */
export function BarChart({
  data,
  series,
  label,
  bucket = "day",
  format = fmt,
  height = 160,
  counts = true,
}: {
  data: Point[];
  series: Series[];
  label: string;
  bucket?: "day" | "week";
  format?: Fmt;
  height?: number;
  /** Whole numbers (tickets, calls): the axis steps stay whole. */
  counts?: boolean;
}) {
  const [hover, setHover] = useState<number | null>(null);
  const totals = data.map((p) => series.reduce((a, s) => a + (Number(p[s.key]) || 0), 0));
  const top = Math.max(0, ...totals);
  const max = counts ? niceCountMax(top) : niceMax(top);
  const sum = totals.reduce((a, b) => a + b, 0);
  const tip = hover === null ? null : data[hover];
  return (
    <figure className="vr-chart" aria-label={label}>
      <Legend series={series} />
      <div className="vr-chart-plot" style={{ height }} onMouseLeave={() => setHover(null)}>
        <div className="vr-chart-grid" aria-hidden="true">
          <span data-v={format(max)} />
          <span data-v={format(max / 2)} />
          <span data-v="0" />
        </div>
        <div className="vr-chart-bars" role="img" aria-label={t("{label}: {sum} in total over {length} {v}", { label: label, sum: format(sum), length: data.length, v: bucket === "week" ? "weeks" : "days" })}>
          {data.map((p, i) => (
            <button
              key={p.date}
              type="button"
              className={cx("vr-chart-col", hover === i && "is-on")}
              aria-label={`${dayLabel(p.date, bucket)}: ${series.map((s) => `${t(s.label)} ${format(Number(p[s.key]) || 0)}`).join(", ")}`}
              onMouseEnter={() => setHover(i)}
              onFocus={() => setHover(i)}
              onBlur={() => setHover(null)}
            >
              <span className="vr-chart-stack" style={{ height: `${(totals[i] / max) * 100}%` }}>
                {series.map((s) => {
                  const v = Number(p[s.key]) || 0;
                  return v > 0 ? <span key={s.key} style={{ flexGrow: v, background: TONE_VAR[s.tone] }} /> : null;
                })}
              </span>
            </button>
          ))}
        </div>
        {tip ? (
          <div className="vr-chart-tip" style={{ left: `${((hover! + 0.5) / data.length) * 100}%` }} role="status">
            <b>{dayLabel(tip.date, bucket, true)}</b>
            {series.map((s) => (
              <span key={s.key}>
                {series.length > 1 ? <i style={{ background: TONE_VAR[s.tone] }} aria-hidden="true" /> : null}
                {t(s.label)} <span className="vr-mono">{format(Number(tip[s.key]) || 0)}</span>
              </span>
            ))}
          </div>
        ) : null}
      </div>
      <Axis data={data} bucket={bucket} />
    </figure>
  );
}

function Axis({ data, bucket }: { data: Point[]; bucket: "day" | "week" }) {
  if (!data.length) return null;
  const mid = data[Math.floor((data.length - 1) / 2)];
  return (
    <div className="vr-chart-axis" aria-hidden="true">
      <span>{dayLabel(data[0].date, bucket)}</span>
      {data.length > 4 ? <span>{dayLabel(mid.date, bucket)}</span> : null}
      <span>{dayLabel(data[data.length - 1].date, bucket)}</span>
    </div>
  );
}

/** One series over time as a 2px line; buckets without a value (null) leave a gap. */
export function LineChart({
  data,
  serie,
  label,
  bucket = "day",
  format = fmt,
  height = 160,
}: {
  data: Point[];
  serie: Series;
  label: string;
  bucket?: "day" | "week";
  format?: Fmt;
  height?: number;
}) {
  const [hover, setHover] = useState<number | null>(null);
  const values = data.map((p) => (p[serie.key] === null || p[serie.key] === undefined ? null : Number(p[serie.key])));
  const max = niceMax(Math.max(0, ...values.map((v) => v ?? 0)));
  const n = data.length;
  const x = (i: number) => (n === 1 ? 50 : (i / (n - 1)) * 100);
  const y = (v: number) => 100 - (v / max) * 100;
  const path = useMemo(() => {
    let d = "";
    let open = false;
    values.forEach((v, i) => {
      if (v === null) {
        open = false;
        return;
      }
      d += `${open ? "L" : "M"}${x(i).toFixed(2)},${y(v).toFixed(2)} `;
      open = true;
    });
    return d;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [data, max]);
  const known = values.filter((v): v is number => v !== null);
  const tipV = hover === null ? null : values[hover];
  return (
    <figure className="vr-chart" aria-label={label}>
      <div className="vr-chart-plot" style={{ height }} onMouseLeave={() => setHover(null)}>
        <div className="vr-chart-grid" aria-hidden="true">
          <span data-v={format(max)} />
          <span data-v={format(max / 2)} />
          <span data-v="0" />
        </div>
        <svg className="vr-chart-line" viewBox="0 0 100 100" preserveAspectRatio="none" aria-hidden="true">
          <path d={path} style={{ stroke: TONE_VAR[serie.tone] }} />
        </svg>
        {values.map((v, i) =>
          v === null || (hover !== i && known.length > 1) ? null : (
            <span key={i} className="vr-chart-dot" style={{ left: `${x(i)}%`, top: `${y(v)}%`, background: TONE_VAR[serie.tone] }} aria-hidden="true" />
          ),
        )}
        {hover !== null ? <span className="vr-chart-cross" style={{ left: `${x(hover)}%` }} aria-hidden="true" /> : null}
        <div
          className="vr-chart-hits"
          role="img"
          aria-label={known.length ? t("{label}: from {from} to {to}", { label, from: format(known[0]), to: format(known[known.length - 1]) }) : t("{label}: no data in this range", { label })}
        >
          {data.map((p, i) => (
            <button
              key={p.date}
              type="button"
              aria-label={`${dayLabel(p.date, bucket)}: ${values[i] === null ? "no data" : format(values[i]!)}`}
              onMouseEnter={() => setHover(i)}
              onFocus={() => setHover(i)}
              onBlur={() => setHover(null)}
            />
          ))}
        </div>
        {hover !== null ? (
          <div className="vr-chart-tip" style={{ left: `${x(hover)}%` }} role="status">
            <b>{dayLabel(data[hover].date, bucket, true)}</b>
            <span>
              {t(serie.label)} <span className="vr-mono">{tipV === null ? t("no data") : format(tipV)}</span>
            </span>
          </div>
        ) : null}
      </div>
      <Axis data={data} bucket={bucket} />
    </figure>
  );
}

/** Horizontal bars for a ranked breakdown (categories, features, tools): one hue, the value always in words. */
export function BarList({
  rows,
  label,
  format = fmt,
  tone = "s2",
  note,
}: {
  rows: { name: string; value: number; note?: ReactNode; tone?: Tone }[];
  label: string;
  format?: Fmt;
  tone?: Tone;
  note?: (row: { name: string; value: number }) => ReactNode;
}) {
  const max = Math.max(1, ...rows.map((r) => r.value));
  const total = rows.reduce((a, r) => a + r.value, 0);
  if (!rows.length) return <p className="vr-muted vr-caption">{t("Nothing in this range.")}</p>;
  return (
    <ul className="vr-barlist" aria-label={label}>
      {rows.map((r) => (
        <li key={r.name}>
          <span className="vr-barlist-name">{r.name}</span>
          <span className="vr-barlist-track" aria-hidden="true">
            <span style={{ width: `${(r.value / max) * 100}%`, background: TONE_VAR[r.tone ?? tone] }} />
          </span>
          <span className="vr-barlist-value vr-mono">
            {format(r.value)}
            {total > 0 && rows.length > 1 ? <span className="vr-muted"> · {Math.round((r.value * 100) / total)}%</span> : null}
          </span>
          {r.note ?? note?.(r) ? <span className="vr-barlist-note vr-muted">{r.note ?? note?.(r)}</span> : null}
        </li>
      ))}
    </ul>
  );
}

/** "Up 12% from the previous 7 days" — direction in words, never colour alone. */
export function delta(current: number | null | undefined, previous: number | null | undefined, days: number) {
  if (current === null || current === undefined || previous === null || previous === undefined) return t("No data for the previous {days} days", { days });
  if (previous === 0) return current === 0 ? t("Same as the previous {days} days", { days }) : t("None in the previous {days} days", { days });
  const pct = Math.round(((current - previous) / previous) * 100);
  if (pct === 0) return t("Same as the previous {days} days", { days });
  return pct > 0 ? t("Up {pct}% from the previous {days} days", { pct, days }) : t("Down {pct}% from the previous {days} days", { pct: Math.abs(pct), days });
}

export function KPI({ label, value, note }: { label: string; value: ReactNode; note?: ReactNode }) {
  return (
    <div className="vr-stat">
      <span className="vr-eyebrow">{label}</span>
      <span className="vr-display vr-stat-num">{value}</span>
      {note ? <span className="vr-help">{note}</span> : null}
    </div>
  );
}

/** A card with a chart and its data as a table, switchable. */
export function ChartCard({
  title,
  subtitle,
  chart,
  table,
  actions,
}: {
  title: string;
  subtitle?: ReactNode;
  chart: ReactNode;
  table: { columns: string[]; rows: (string | number)[][] };
  actions?: ReactNode;
}) {
  const [view, setView] = useState("chart");
  const id = useId();
  return (
    <section className="vr-panel vr-chart-card" aria-labelledby={id}>
      <header className="vr-panel-head">
        <div className="vr-chart-title">
          <h2 className="vr-h" id={id}>
            {title}
          </h2>
          {subtitle ? <span className="vr-help">{subtitle}</span> : null}
        </div>
        <div className="vr-actions">
          {actions}
          <Segmented
            label={t("{title}: chart or table", { title: title })}
            value={view}
            onChange={setView}
            options={[
              { value: "chart", label: t("Chart") },
              { value: "table", label: t("Table") },
            ]}
          />
        </div>
      </header>
      <div className="vr-panel-body">
        {view === "chart" ? (
          chart
        ) : (
          <div className="vr-table-wrap vr-chart-table">
            <table className="vr-table">
              <thead>
                <tr>
                  {table.columns.map((c, i) => (
                    <th key={c} className={i ? "num" : undefined}>
                      {c}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {table.rows.map((r, i) => (
                  <tr key={i}>
                    {r.map((v, j) => (
                      <td key={j} className={j ? "num" : undefined}>
                        {v}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </section>
  );
}

/** Rows for ChartCard's table view from a series. */
export function seriesTable(data: Point[], series: Series[], bucket: "day" | "week", format: Fmt = fmt) {
  return {
    columns: [bucket === "week" ? "Week" : "Day", ...series.map((s) => s.label)],
    rows: data.map((p) => [dayLabel(p.date, bucket, true), ...series.map((s) => (p[s.key] === null ? "—" : format(Number(p[s.key]) || 0)))]),
  };
}

export function minutes(m: number | null | undefined) {
  if (m === null || m === undefined) return "—";
  if (m === 0) return t("{v} min", { v: 0 });
  if (m < 1) return t("{v} s", { v: Math.max(1, Math.round(m * 60)) });
  if (m < 60) return t("{v} min", { v: Math.round(m) });
  if (m < 48 * 60) return t("{v} h", { v: (m / 60).toFixed(m < 600 ? 1 : 0) });
  return t("{v} d", { v: (m / 1440).toFixed(1) });
}

export function pct(v: number | null | undefined) {
  return v === null || v === undefined ? "—" : `${v}%`;
}
