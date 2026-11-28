/* Interface languages. English is the source: the English text is the key, so a string missing from another
   language's dictionary shows in English rather than as a key. `npm run i18n` extracts every t()/tp()/msg()
   string and checks each dictionary has it with the same {placeholders}.

   The current locale is set (and its dictionary loaded) before anything renders: the app waits for the session,
   and auth pages wait for mount. t() reads it, so it also works in plain functions; strings at module level are
   marked with msg() and translated with t() where they're shown. */

export const LOCALES = [
  { value: "en", label: "English" },
  { value: "es", label: "Español" },
  { value: "de", label: "Deutsch" },
  { value: "fr", label: "Français" },
  { value: "it", label: "Italiano" },
  { value: "ar", label: "العربية" },
] as const;
export type Locale = (typeof LOCALES)[number]["value"];
export type Plural = Partial<Record<Intl.LDMLPluralRule, string>>;
export type Dict = Record<string, string | Plural>;

export const RTL = new Set<Locale>(["ar"]);
const LOADERS: Record<Exclude<Locale, "en">, () => Promise<{ default: Dict }>> = {
  es: () => import("./locales/es.json"),
  de: () => import("./locales/de.json"),
  fr: () => import("./locales/fr.json"),
  it: () => import("./locales/it.json"),
  ar: () => import("./locales/ar.json"),
};

let current: Locale = "en";
let dict: Dict = {};
let rules = new Intl.PluralRules("en");

export function isLocale(v: unknown): v is Locale {
  return LOCALES.some((l) => l.value === v);
}

export function locale(): Locale {
  return current;
}

/** BCP 47 tag for Intl formatting (Western digits in Arabic, as the rest of the product shows). */
export function intlLocale(): string {
  return current === "ar" ? "ar-u-nu-latn" : current === "en" ? "en-GB" : current;
}

/** BCP 47 tag for numbers and money: as intlLocale(), but English uses en-US ("1M", "$29" rather than "1m", "US$29"). */
export function numberLocale(): string {
  return current === "en" ? "en-US" : intlLocale();
}

export function isRtl(): boolean {
  return RTL.has(current);
}

/** Loads a language and makes it current; also sets <html lang dir> and remembers it for the sign-in pages. */
export async function setLocale(next: string | null | undefined): Promise<Locale> {
  const l: Locale = isLocale(next) ? next : "en";
  dict = l === "en" ? {} : (await LOADERS[l]()).default;
  current = l;
  rules = new Intl.PluralRules(l);
  if (typeof document !== "undefined") {
    document.documentElement.lang = l;
    document.documentElement.dir = RTL.has(l) ? "rtl" : "ltr";
    try {
      localStorage.setItem("verity.locale", l);
    } catch {
      /* private mode */
    }
  }
  return l;
}

/** The locale remembered on this device, or the browser's language when it's one we have. */
export function preferredLocale(): Locale {
  try {
    const saved = localStorage.getItem("verity.locale");
    if (isLocale(saved)) return saved;
  } catch {
    /* private mode */
  }
  const nav = typeof navigator !== "undefined" ? navigator.language.slice(0, 2) : "en";
  return isLocale(nav) ? nav : "en";
}

function fill(s: string, vars?: Record<string, string | number | null | undefined>): string {
  if (!vars) return s;
  return s.replace(/\{(\w+)\}/g, (m, k: string) => (k in vars ? String(vars[k] ?? "") : m));
}

type Vars = Record<string, string | number | null | undefined>;
export type Translator = {
  t: (text: string, vars?: Vars) => string;
  tp: (one: string, other: string, n: number, vars?: Vars) => string;
};

/** A translator bound to one dictionary: no shared state, so it is safe where several languages render at once (the server). */
export function makeTranslator(d: Dict, l: Locale): Translator {
  const pr = new Intl.PluralRules(l);
  return {
    t(text, vars) {
      const hit = d[text];
      return fill(typeof hit === "string" ? hit : text, vars);
    },
    tp(one, other, n, vars) {
      const hit = d[`${one}|${other}`];
      const all = { n, ...vars };
      if (hit && typeof hit === "object") return fill(hit[pr.select(n)] ?? hit.other ?? other, all);
      return fill(n === 1 ? one : other, all);
    },
  };
}

/** Translates `text` (the English source) and fills {placeholders}. */
export function t(text: string, vars?: Vars): string {
  const hit = dict[text];
  return fill(typeof hit === "string" ? hit : text, vars);
}

/** A count: "{n} ticket" / "{n} tickets", with each language's own plural forms (Arabic has six). */
export function tp(one: string, other: string, n: number, vars?: Vars): string {
  const hit = dict[`${one}|${other}`];
  const all = { n, ...vars };
  if (hit && typeof hit === "object") {
    const form = hit[rules.select(n)] ?? hit.other ?? other;
    return fill(form, all);
  }
  return fill(n === 1 ? one : other, all);
}

/** Marks a string for translation without translating it (module-level constants); show it with t(). */
export function msg<T extends string>(text: T): T {
  return text;
}
