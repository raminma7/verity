"use client";

import { useEffect, useState } from "react";

import { Icon } from "@/components/ui";

/** Light/dark switch with the same storage key as the dashboard's (the saved theme is applied before paint by the
 *  root layout's script). Labels come from the server, in the page's language. */
export function ThemeToggle({ light, dark, label }: { light: string; dark: string; label: string }) {
  const [theme, setTheme] = useState<"light" | "dark" | null>(null);
  useEffect(() => {
    const t = document.documentElement.dataset.theme;
    setTheme(t === "dark" || (!t && matchMedia("(prefers-color-scheme: dark)").matches) ? "dark" : "light");
  }, []);
  function toggle() {
    const next = theme === "dark" ? "light" : "dark";
    document.documentElement.dataset.theme = next;
    try {
      localStorage.setItem("verity.theme", next);
    } catch {
      /* private mode */
    }
    setTheme(next);
  }
  const target = theme === "dark" ? light : dark;
  return (
    <button type="button" className="vr-btn vr-btn-ghost vr-btn-sm vr-btn-icon" onClick={toggle} aria-label={`${label}: ${target}`} title={target}>
      <Icon name={theme === "dark" ? "sun" : "moon"} size={16} />
    </button>
  );
}
