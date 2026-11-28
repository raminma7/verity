/**
 * The Verity logo, "Two voices": two overlapping speech bubbles with a lime lens where they meet, and the wordmark
 * in Geist 600. The SVG is inline (no files to load) and decorative; the wrapper is the image named "Verity".
 *
 * theme "auto" follows the active light/dark theme through the logo tokens; "light", "dark" and "teal" are the design
 * system's fixed palettes for a known background ("teal" is for the brand-coloured panel). The lens is always lime.
 * `size` is the wordmark's font size in px: the symbol is 0.92 × that, and the gap 0.23 ×.
 */
/* A local cx: this file is used by server components, and ui.tsx is a client module. */
const cx = (...p: Array<string | false | null | undefined>) => p.filter(Boolean).join(" ");
const LENS = "#d6f35b";
const PALETTE = {
  light: { b1: "#0d6655", b2: "#15171a", word: "#15171a" },
  dark: { b1: "#4fd6b9", b2: "#f5f3ed", word: "#eef0ea" },
  teal: { b1: "#f5f3ed", b2: "#15171a", word: "#f5f3ed" },
  auto: { b1: "var(--logo-bubble-1)", b2: "var(--logo-bubble-2)", word: "var(--logo-wordmark)" },
} as const;

export type LogoTheme = keyof typeof PALETTE;

export function LogoSymbol({ size, theme = "auto" }: { size: number; theme?: LogoTheme }) {
  const c = PALETTE[theme];
  return (
    <svg width={size} height={size} viewBox="0 0 100 100" xmlns="http://www.w3.org/2000/svg" aria-hidden="true" focusable="false" style={{ flex: "none", display: "block" }}>
      <path d="M0 64 A36 36 0 0 1 36 28 A36 36 0 0 1 72 64 A36 36 0 0 1 36 100 L0 100 Z" fill={c.b1} />
      <path d="M28 36 A36 36 0 0 1 64 0 L100 0 L100 36 A36 36 0 0 1 64 72 A36 36 0 0 1 28 36 Z" fill={c.b2} />
      <path d="M28.74 28.74 A36 36 0 0 1 71.26 71.26 A36 36 0 0 1 28.74 28.74 Z" fill={LENS} />
    </svg>
  );
}

export function Logo({ variant = "full", theme = "auto", size = 30, className }: { variant?: "full" | "symbol"; theme?: LogoTheme; size?: number; className?: string }) {
  const c = PALETTE[theme];
  if (variant === "symbol") {
    return (
      <span className={cx("vr-logo", className)} role="img" aria-label="Verity">
        <LogoSymbol size={size} theme={theme} />
      </span>
    );
  }
  return (
    <span className={cx("vr-logo", className)} role="img" aria-label="Verity" style={{ gap: size * 0.23 }}>
      <LogoSymbol size={Math.round(size * 0.92 * 100) / 100} theme={theme} />
      <span aria-hidden="true" dir="ltr" style={{ fontFamily: "var(--font-sans)", fontWeight: 600, fontSize: size, lineHeight: 1, letterSpacing: "-0.045em", color: c.word }}>
        Verity
      </span>
    </span>
  );
}
