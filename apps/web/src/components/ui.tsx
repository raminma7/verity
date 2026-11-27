"use client";

import { intlLocale, t, msg } from "@/i18n";
/**
 * Verity design system components, ported from the design system bundle (window.Verity) to typed React.
 * Class names (vr-*) and behaviour match the bundle so src/design/verity.css styles them unchanged.
 */
import { useState, type ButtonHTMLAttributes, type InputHTMLAttributes, type ReactNode } from "react";

export function cx(...parts: Array<string | false | null | undefined>) {
  return parts.filter(Boolean).join(" ");
}

const PATHS = {
  send: msg("M5 12h13M12 5l7 7-7 7"),
  copy: msg("M9 9h10v10H9zM5 15V5h10"),
  refresh: msg("M20 11a8 8 0 1 0-2.3 5.7M20 5v6h-6"),
  check: msg("M5 12.5l4.5 4.5L19 7"),
  spark: msg("M12 3v4M12 17v4M3 12h4M17 12h4M6 6l2.5 2.5M15.5 15.5L18 18M6 18l2.5-2.5M15.5 8.5L18 6"),
  x: msg("M6 6l12 12M18 6L6 18"),
  search: msg("M11 18a7 7 0 1 1 0-14 7 7 0 0 1 0 14zM16.5 16.5L21 21"),
  upload: msg("M12 16V4M6 10l6-6 6 6M4 20h16"),
  dots: msg("M5 12h.01M12 12h.01M19 12h.01"),
  mail: msg("M4 7l8 6 8-6M4 6h16v12H4z"),
  lock: msg("M6 11h12v9H6zM9 11V8a3 3 0 0 1 6 0v3"),
  plus: msg("M12 5v14M5 12h14"),
  chat: msg("M21 12a8 8 0 0 1-11.6 7.1L4 20l1-4.6A8 8 0 1 1 21 12z"),
  book: msg("M5 4h11a3 3 0 0 1 3 3v13H8a3 3 0 0 1-3-3zM5 17a3 3 0 0 1 3-3h11"),
  clock: msg("M12 21a9 9 0 1 1 0-18 9 9 0 0 1 0 18zM12 7v5l3 2"),
  sliders: msg("M4 6h10M18 6h2M4 12h4M12 12h8M4 18h12M20 18h0M14 4v4M8 10v4M16 16v4"),
  arrow: msg("M5 12h14M13 6l6 6-6 6"),
  shield: msg("M12 3l8 3v6c0 4.5-3.4 8.3-8 9-4.6-.7-8-4.5-8-9V6z"),
  menu: msg("M4 7h16M4 12h16M4 17h10"),
  file: msg("M7 3h7l5 5v13H7zM14 3v5h5"),
  link: msg("M10 14a4 4 0 0 0 5.7 0l3-3a4 4 0 0 0-5.7-5.7l-1 1M14 10a4 4 0 0 0-5.7 0l-3 3a4 4 0 0 0 5.7 5.7l1-1"),
  // Added for the support platform, drawn on the same 24px grid and 1.8 stroke.
  home: msg("M4 10.5L12 4l8 6.5V20H4z"),
  gear: msg("M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6zM12 2v3M12 19v3M4.9 4.9l2.1 2.1M17 17l2.1 2.1M2 12h3M19 12h3M4.9 19.1L7 17M17 7l2.1-2.1"),
  card: msg("M3 6h18v12H3zM3 10h18M7 15h4"),
  users: msg("M9 11a4 4 0 1 0 0-8 4 4 0 0 0 0 8zM2 21a7 7 0 0 1 14 0M16 3.5a4 4 0 0 1 0 7M22 21a7 7 0 0 0-4-6.3"),
  chart: msg("M4 20V10M10 20V4M16 20v-7M22 20H2"),
  tag: msg("M3 12V4h8l10 10-8 8zM7.5 8.5h.01"),
  logout: msg("M15 17l5-5-5-5M20 12H9M12 21H4V3h8"),
  sun: msg("M12 17a5 5 0 1 0 0-10 5 5 0 0 0 0 10zM12 1v2M12 21v2M4.2 4.2l1.4 1.4M18.4 18.4l1.4 1.4M1 12h2M21 12h2M4.2 19.8l1.4-1.4M18.4 5.6l1.4-1.4"),
  moon: msg("M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z"),
  alert: msg("M12 9v4M12 17h.01M10.3 3.9L1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z"),
  chevron: msg("M6 9l6 6 6-6"),
  building: msg("M4 21V3h11v18M15 9h5v12M8 7h3M8 11h3M8 15h3M4 21h17"),
} as const;

export type IconName = keyof typeof PATHS;

export function Icon({ name, size = 16, className }: { name: IconName; size?: number; className?: string }) {
  return (
    <svg
      className={cx("vr-icon", className)}
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.8}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d={PATHS[name]} />
    </svg>
  );
}

type ButtonProps = ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: "primary" | "secondary" | "ghost" | "danger" | "ink";
  size?: "md" | "sm" | "lg";
  icon?: IconName;
  iconRight?: IconName;
  busy?: boolean;
};

/** "ink" is the sign-in button from the WebAuth screen: ink fill on paper. */
export function Button({ variant = "secondary", size = "md", icon, iconRight, busy, className, children, disabled, type = "button", ...rest }: ButtonProps) {
  return (
    <button
      type={type}
      {...rest}
      disabled={disabled || busy}
      aria-busy={busy || undefined}
      className={cx("vr-btn", `vr-btn-${variant}`, `vr-btn-${size}`, !children && "vr-btn-icon", className)}
    >
      {busy ? <span className="vr-spin vr-spin-sm" aria-hidden="true" /> : icon ? <Icon name={icon} /> : null}
      {children ? <span>{children}</span> : null}
      {iconRight ? <Icon name={iconRight} /> : null}
    </button>
  );
}

export type Tone = "neutral" | "brand" | "warn" | "danger" | "highlight";

export function Badge({ tone = "neutral", dot, children }: { tone?: Tone; dot?: boolean; children: ReactNode }) {
  return (
    <span className={cx("vr-badge", `vr-badge-${tone}`)}>
      {dot ? <span className="vr-badge-dot" aria-hidden="true" /> : null}
      {children}
    </span>
  );
}

type FieldProps = InputHTMLAttributes<HTMLInputElement> & {
  label?: ReactNode;
  help?: ReactNode;
  error?: string | null;
  icon?: IconName;
  trailing?: ReactNode;
  inputSize?: "md" | "lg";
  prefix?: ReactNode;
};

export function Field({ label, help, error, icon, trailing, inputSize, prefix, className, id, ...rest }: FieldProps) {
  const errId = error ? `${id ?? rest.name}-error` : undefined;
  return (
    <label className={cx("vr-field", className)}>
      {label ? (
        <span className="vr-field-top">
          {label}
          {trailing}
        </span>
      ) : null}
      <span className={cx("vr-input", inputSize === "lg" && "vr-input-lg", error && "is-error")}>
        {icon ? <Icon name={icon} /> : null}
        {prefix}
        <input type="text" id={id} aria-invalid={error ? true : undefined} aria-describedby={errId} {...rest} />
      </span>
      {error ? (
        <span className="vr-help vr-help-error" id={errId}>
          {error}
        </span>
      ) : help ? (
        <span className="vr-help">{help}</span>
      ) : null}
    </label>
  );
}

export function Select({
  label,
  help,
  value,
  onChange,
  options,
  name,
  disabled,
  ariaLabel,
}: {
  label?: ReactNode;
  help?: ReactNode;
  value: string;
  onChange: (v: string) => void;
  options: { value: string; label: string }[];
  name?: string;
  disabled?: boolean;
  /** For a select without a visible label. */
  ariaLabel?: string;
}) {
  return (
    <label className="vr-field">
      {label ? <span className="vr-field-top">{label}</span> : null}
      <span className="vr-input vr-select">
        <select name={name} value={value} disabled={disabled} aria-label={ariaLabel} onChange={(e) => onChange(e.target.value)}>
          {options.map((o) => (
            <option key={o.value} value={o.value}>
              {t(o.label)}
            </option>
          ))}
        </select>
        <Icon name="chevron" />
      </span>
      {help ? <span className="vr-help">{help}</span> : null}
    </label>
  );
}

export function Segmented({
  options,
  value,
  onChange,
  block,
  label,
}: {
  options: { value: string; label: string }[];
  value: string;
  onChange: (v: string) => void;
  block?: boolean;
  label?: string;
}) {
  return (
    <div className={cx("vr-seg", block && "vr-seg-block")} role="tablist" aria-label={label}>
      {options.map((o) => (
        <button key={o.value} type="button" role="tab" aria-selected={value === o.value} className={cx(value === o.value && "is-on")} onClick={() => onChange(o.value)}>
          {t(o.label)}
        </button>
      ))}
    </div>
  );
}

export function Switch({
  label,
  help,
  checked,
  onChange,
  disabled,
}: {
  label?: string;
  help?: string;
  checked: boolean;
  onChange: (on: boolean) => void;
  disabled?: boolean;
}) {
  const sw = (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={label}
      disabled={disabled}
      className={cx("vr-switch", checked && "is-on")}
      onClick={() => onChange(!checked)}
    />
  );
  if (!label) return sw;
  return (
    <div className="vr-setting">
      <span className="vr-setting-text">
        <b>{label}</b>
        {help ? <span>{help}</span> : null}
      </span>
      {sw}
    </div>
  );
}

/** Usage bar in the RelevanceMeter's style. Colour carries meaning, so the number and a word always sit beside it. */
export function UsageMeter({ used, total, label }: { used: number; total: number; label?: string }) {
  const unlimited = total < 0;
  const pct = unlimited || total === 0 ? 0 : Math.min(100, (used / total) * 100);
  const tier = pct >= 90 ? "danger" : pct >= 80 ? "warn" : "brand";
  return (
    <div className="vr-usage">
      {label ? (
        <div className="vr-usage-top">
          <span>{label}</span>
          <span className="vr-mono">
            {fmt(used)} / {unlimited ? t("Unlimited") : fmt(total)}
          </span>
        </div>
      ) : null}
      <div
        className={cx("vr-usage-track", `is-${tier}`)}
        role="meter"
        aria-valuemin={0}
        aria-valuemax={unlimited ? undefined : total}
        aria-valuenow={used}
        aria-label={label ?? t("Usage")}
      >
        <span style={{ width: `${pct}%` }} />
      </div>
    </div>
  );
}

export function Banner({
  tone = "brand",
  icon,
  title,
  children,
  action,
}: {
  tone?: "brand" | "warn" | "danger" | "neutral";
  icon?: IconName;
  title?: ReactNode;
  children?: ReactNode;
  action?: ReactNode;
}) {
  return (
    <div className={cx("vr-banner", `vr-banner-${tone}`)} role={tone === "danger" ? "alert" : "status"}>
      <Icon name={icon ?? (tone === "brand" ? "check" : tone === "neutral" ? "spark" : "alert")} />
      <div className="vr-banner-body">
        {title ? <b>{title}</b> : null}
        {children ? <span>{children}</span> : null}
      </div>
      {action}
    </div>
  );
}

export function PageHeader({ eyebrow, title, subtitle, actions }: { eyebrow?: string; title: string; subtitle?: ReactNode; actions?: ReactNode }) {
  return (
    <header className="vr-pagehead">
      <div>
        {eyebrow ? <span className="vr-eyebrow">{eyebrow}</span> : null}
        <h1 className="vr-display vr-title">{title}</h1>
        {subtitle ? <p className="vr-muted vr-pagehead-sub">{subtitle}</p> : null}
      </div>
      {actions ? <div className="vr-pagehead-actions">{actions}</div> : null}
    </header>
  );
}

export function Card({ title, actions, children, className, padded = true }: { title?: ReactNode; actions?: ReactNode; children: ReactNode; className?: string; padded?: boolean }) {
  return (
    <section className={cx("vr-card", "vr-panel", className)}>
      {title || actions ? (
        <header className="vr-panel-head">
          {title ? <h2 className="vr-h">{title}</h2> : <span />}
          {actions}
        </header>
      ) : null}
      <div className={cx(padded && "vr-panel-body")}>{children}</div>
    </section>
  );
}

export function Stat({ label, value, note }: { label: string; value: ReactNode; note?: ReactNode }) {
  return (
    <div className="vr-stat">
      <span className="vr-eyebrow">{label}</span>
      <span className="vr-display vr-stat-num">{value}</span>
      {note ? <span className="vr-help">{note}</span> : null}
    </div>
  );
}

export function EmptyState({ icon = "spark", title, children, action }: { icon?: IconName; title: string; children?: ReactNode; action?: ReactNode }) {
  return (
    <div className="vr-empty">
      <span className="vr-empty-icon">
        <Icon name={icon} size={20} />
      </span>
      <b>{title}</b>
      {children ? <span className="vr-muted">{children}</span> : null}
      {action}
    </div>
  );
}

export function Spinner({ label = "Loading" }: { label?: string }) {
  return (
    <div className="vr-loading" role="status">
      <span className="vr-spin" aria-hidden="true" />
      <span className="vr-muted">{label}…</span>
    </div>
  );
}

export function Avatar({ name }: { name: string }) {
  const initials = name
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((p) => p[0]?.toUpperCase())
    .join("");
  return <span className="vr-avatar">{initials || "?"}</span>;
}

export function useToggle(initial = false) {
  const [on, set] = useState(initial);
  return [on, () => set((v) => !v), set] as const;
}

export function fmt(n: number) {
  return new Intl.NumberFormat(intlLocale()).format(n);
}

export function compact(n: number) {
  return new Intl.NumberFormat(intlLocale(), { notation: "compact", maximumFractionDigits: 1 }).format(n);
}

export function money(cents: number, currency = "USD") {
  return new Intl.NumberFormat(intlLocale(), { style: "currency", currency, minimumFractionDigits: cents % 100 ? 2 : 0 }).format(cents / 100);
}

export function date(iso: string | null | undefined) {
  if (!iso) return "—";
  return new Date(iso).toLocaleDateString(intlLocale(), { day: "numeric", month: "short", year: "numeric" });
}

export function dateTime(iso: string | null | undefined) {
  if (!iso) return "—";
  return new Date(iso).toLocaleString(intlLocale(), { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
}
