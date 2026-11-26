// Generates src/design/tokens.css from src/design/tokens.json (copied from the Verity design system).
// tokens.json is the single source of truth: edit it, then run `npm run tokens`.
import { readFileSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const dir = join(dirname(fileURLToPath(import.meta.url)), "..", "src", "design");
const t = JSON.parse(readFileSync(join(dir, "tokens.json"), "utf8"));
const themes = t.color.themes.map((x) => x.id);
const first = themes[0];

const resolve = (v) => (typeof v === "string" && v.startsWith("{") ? `var(--${v.slice(1, -1)})` : v);
const valueFor = (tok, theme) =>
  resolve(typeof tok.value === "object" ? (tok.value[theme] ?? tok.value[first]) : tok.value);

function block(theme) {
  const lines = [];
  for (const tok of t.color.tokens) lines.push(`  --${tok.name}: ${valueFor(tok, theme)};`);
  for (const tok of t.shadow?.tokens ?? []) lines.push(`  --${tok.name}: ${valueFor(tok, theme)};`);
  return lines.join("\n");
}

const statics = [];
for (const [name, stack] of Object.entries(t.type.families)) statics.push(`  --font-${name}: ${stack};`);
for (const fam of ["spacing", "radius"]) for (const tok of t[fam].tokens) statics.push(`  --${tok.name}: ${tok.value};`);
for (const g of t.type.groups)
  for (const s of g.styles) {
    statics.push(`  --text-${s.name}-size: ${s.fontSize};`);
    statics.push(`  --text-${s.name}-line: ${s.lineHeight};`);
  }

const dark = block("dark");
const css = `/* Verity — generated from tokens.json by scripts/build-tokens.mjs. Do not edit. */
:root {
  color-scheme: light;
${statics.join("\n")}
${block(first)}
}
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) {
    color-scheme: dark;
${dark.replace(/^/gm, "  ")}
  }
}
:root[data-theme="dark"] {
  color-scheme: dark;
${dark}
}
`;
writeFileSync(join(dir, "tokens.css"), css);
console.log("wrote src/design/tokens.css");
