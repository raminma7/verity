"use client";

import { createContext, useContext, useMemo, type ReactNode } from "react";

import { makeTranslator, t as globalT, tp as globalTp, type Dict, type Locale, type Translator } from "./index";

/* The dashboard translates through the module-level t(), set once per browser tab. A page rendered on the server
   for several languages at once can't share that state, so it wraps the product components it shows in an
   I18nScope: they then read their strings from the scope's own small dictionary. Outside a scope, nothing changes. */
const Scope = createContext<Translator | null>(null);

export function I18nScope({ locale, strings, children }: { locale: Locale; strings: Dict; children: ReactNode }) {
  const tr = useMemo(() => makeTranslator(strings, locale), [strings, locale]);
  return <Scope.Provider value={tr}>{children}</Scope.Provider>;
}

export function useTr(): Translator["t"] {
  return useContext(Scope)?.t ?? globalT;
}

export function useTp(): Translator["tp"] {
  return useContext(Scope)?.tp ?? globalTp;
}
