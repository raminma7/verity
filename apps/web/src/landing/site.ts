import { LOCALES, type Locale } from "@/i18n";

/** The languages the public pages exist in; English is served at "/", the others under /es, /de, /fr, /it, /ar. */
export const SITE_LOCALES: Locale[] = LOCALES.map((l) => l.value);

/** Absolute site address for canonical links, hreflang and the sitemap: VERITY_SITE_URL, else VERITY_APP_URL. */
export function siteUrl(): string {
  return (process.env.VERITY_SITE_URL || process.env.VERITY_APP_URL || "http://localhost:3000").replace(/\/+$/, "");
}

export function apiUrl(): string {
  return process.env.VERITY_API_URL ?? "http://localhost:8000";
}

/** The path of a public page in a language: English has no prefix. */
export function pathFor(locale: Locale, page: "" | "/privacy" = ""): string {
  return locale === "en" ? page || "/" : `/${locale}${page}`;
}

export const OG_LOCALE: Record<Locale, string> = { en: "en_US", es: "es_ES", de: "de_DE", fr: "fr_FR", it: "it_IT", ar: "ar_AR" };

/** hreflang alternates for a public page, including x-default (English). */
export function alternates(page: "" | "/privacy" = ""): Record<string, string> {
  const out: Record<string, string> = {};
  for (const l of SITE_LOCALES) out[l] = siteUrl() + pathFor(l, page);
  out["x-default"] = siteUrl() + pathFor("en", page);
  return out;
}
