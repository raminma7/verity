"use client";

import Link from "next/link";
import { useState } from "react";

import { Button, Field, Segmented } from "@/components/ui";

/** What every analytics endpoint says about the range it covered. */
export type RangeOut = { preset: string; from: string; to: string; days: number; tz: string; bucket: "day" | "week" };
export type RangeValue = { preset: string; from: string; to: string };

const PRESETS = [
  { value: "7d", label: "7 days" },
  { value: "30d", label: "30 days" },
  { value: "90d", label: "90 days" },
  { value: "custom", label: "Custom" },
];

export function RangePicker({ value, onChange, range }: { value: RangeValue; onChange: (v: RangeValue) => void; range?: RangeOut }) {
  const [from, setFrom] = useState(value.from);
  const [to, setTo] = useState(value.to);
  return (
    <div className="vr-range">
      <Segmented label="Time range" value={value.preset} onChange={(p) => onChange(p === "custom" ? { preset: p, from: from || range?.from || "", to: to || range?.to || "" } : { ...value, preset: p })} options={PRESETS} />
      {value.preset === "custom" ? (
        <>
          <Field label="From" type="date" value={from} max={to || undefined} onChange={(e) => setFrom(e.target.value)} />
          <Field label="To" type="date" value={to} min={from || undefined} onChange={(e) => setTo(e.target.value)} />
          <Button onClick={() => onChange({ preset: "custom", from, to })} disabled={!from || !to}>
            Apply
          </Button>
        </>
      ) : null}
      {range ? (
        <span className="vr-range-note">
          {range.days} days · {range.bucket === "week" ? "weekly" : "daily"} · times in {range.tz} (<Link href="/settings">change</Link>)
        </span>
      ) : null}
    </div>
  );
}

export function rangeQuery(v: RangeValue) {
  return v.preset === "custom" ? `range=custom&from=${v.from}&to=${v.to}` : `range=${v.preset}`;
}

