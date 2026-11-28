import type { Locale } from "@/i18n";

/* Numbers and money for the public pages. They take the language explicitly (the dashboard's helpers read global
   state), and match the dashboard: Western digits in Arabic, "$29" rather than "US$29" in English. */
export function tag(locale: Locale): string {
  return locale === "ar" ? "ar-u-nu-latn" : locale === "en" ? "en-US" : locale;
}

export function money(cents: number, currency: string, locale: Locale): string {
  return new Intl.NumberFormat(tag(locale), { style: "currency", currency, currencyDisplay: "narrowSymbol", minimumFractionDigits: cents % 100 ? 2 : 0 }).format(cents / 100);
}

export function compact(n: number, locale: Locale): string {
  return new Intl.NumberFormat(tag(locale), { notation: "compact", maximumFractionDigits: 1 }).format(n);
}

export function whole(n: number, locale: Locale): string {
  return new Intl.NumberFormat(tag(locale)).format(n);
}

export function storage(mb: number, locale: Locale): string {
  const gb = mb >= 1024;
  return new Intl.NumberFormat(tag(locale), { style: "unit", unit: gb ? "gigabyte" : "megabyte", unitDisplay: "short", maximumFractionDigits: 1 }).format(gb ? mb / 1024 : mb);
}
