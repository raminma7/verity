import { makeTranslator, type Dict, type Locale, type Translator } from "./index";

/* Server-side translators for pages that render in a language chosen per request (the landing page). Dictionaries
   are read once and never change, and each translator is bound to its own, so concurrent requests can't mix languages. */
const LOADERS: Record<Exclude<Locale, "en">, () => Promise<{ default: Dict }>> = {
  es: () => import("./locales/es.json"),
  de: () => import("./locales/de.json"),
  fr: () => import("./locales/fr.json"),
  it: () => import("./locales/it.json"),
  ar: () => import("./locales/ar.json"),
};
const cache = new Map<Locale, Promise<{ dict: Dict; tr: Translator }>>();

export function load(locale: Locale) {
  let hit = cache.get(locale);
  if (!hit) {
    hit = (locale === "en" ? Promise.resolve({} as Dict) : LOADERS[locale]().then((m) => m.default)).then((dict) => ({ dict, tr: makeTranslator(dict, locale) }));
    cache.set(locale, hit);
  }
  return hit;
}

export async function getTranslator(locale: Locale): Promise<Translator & { dict: Dict }> {
  const { dict, tr } = await load(locale);
  return { ...tr, dict };
}

/** The part of a dictionary a client component needs, for an I18nScope. */
export function pick(dict: Dict, keys: readonly string[]): Dict {
  const out: Dict = {};
  for (const k of keys) if (k in dict) out[k] = dict[k];
  return out;
}
