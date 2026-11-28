/* eslint-disable @typescript-eslint/no-require-imports */
/* Extracts every interface string (t("…"), tr("…"), msg("…"), tp("…", "…")) into src/i18n/en.json and checks
   that each language's dictionary has all of them with the same {placeholders}.
   npm run i18n            check (fails on anything missing; used in CI)
   npm run i18n -- --write also rewrites en.json and lists what's missing per language */
const ts = require("typescript");
const fs = require("fs");
const path = require("path");

const LOCALES = ["es", "de", "fr", "it", "ar"];
const DIR = path.join(__dirname, "..", "src", "i18n");

function walk(d, out = []) {
  for (const f of fs.readdirSync(d)) {
    const p = path.join(d, f);
    if (fs.statSync(p).isDirectory()) walk(p, out);
    else if (/\.(tsx|ts)$/.test(p) && !p.includes(`${path.sep}i18n${path.sep}`)) out.push(p);
  }
  return out;
}

function lit(n) {
  return n && (ts.isStringLiteral(n) || ts.isNoSubstitutionTemplateLiteral(n)) ? n.text : null;
}

const keys = new Map(); // key → "string" | "plural"
const dynamic = [];
for (const f of walk(path.join(__dirname, "..", "src"))) {
  const sf = ts.createSourceFile(f, fs.readFileSync(f, "utf8"), ts.ScriptTarget.Latest, true, f.endsWith(".tsx") ? ts.ScriptKind.TSX : ts.ScriptKind.TS);
  (function v(n) {
    if (ts.isCallExpression(n) && ts.isIdentifier(n.expression)) {
      const name = n.expression.text;
      if (["t", "tr", "msg"].includes(name) && n.arguments.length) {
        const s = lit(n.arguments[0]);
        if (s !== null) keys.set(s, "string");
        else if (name !== "msg") dynamic.push(`${path.relative(process.cwd(), f)}:${sf.getLineAndCharacterOfPosition(n.getStart()).line + 1}`);
      }
      if (name === "tp" && n.arguments.length >= 2) {
        const a = lit(n.arguments[0]);
        const b = lit(n.arguments[1]);
        if (a !== null && b !== null) keys.set(`${a}|${b}`, "plural");
      }
    }
    ts.forEachChild(n, v);
  })(sf);
}

// The API's own error messages (ApiError(status, "code", "message")), shown through errorText().
const API = path.join(__dirname, "..", "..", "api", "app");
function walkPy(d, out = []) {
  for (const f of fs.readdirSync(d)) {
    const p = path.join(d, f);
    if (fs.statSync(p).isDirectory()) walkPy(p, out);
    else if (p.endsWith(".py")) out.push(p);
  }
  return out;
}
if (fs.existsSync(API)) {
  for (const f of walkPy(API)) {
    const src = fs.readFileSync(f, "utf8");
    for (const m of src.matchAll(/ApiError\(\s*\d+,\s*"[a-z_]+",\s*"((?:[^"\\]|\\.)*)"/gs)) keys.set(JSON.parse(`"${m[1]}"`), "string");
  }
}

const placeholders = (s) => [...String(s).matchAll(/\{(\w+)\}/g)].map((m) => m[1]).sort().join(",");
const sorted = [...keys.keys()].sort((a, b) => a.localeCompare(b));
const write = process.argv.includes("--write");
if (write) fs.writeFileSync(path.join(DIR, "en.json"), JSON.stringify(Object.fromEntries(sorted.map((k) => [k, keys.get(k)])), null, 1) + "\n");

let problems = 0;
for (const l of LOCALES) {
  const dict = JSON.parse(fs.readFileSync(path.join(DIR, "locales", `${l}.json`), "utf8"));
  const missing = [];
  const bad = [];
  for (const k of sorted) {
    const v = dict[k];
    if (v === undefined) {
      missing.push(k);
      continue;
    }
    if (keys.get(k) === "plural") {
      const [one, other] = k.split("|");
      if (typeof v !== "object" || !v.other) bad.push(`${k} (plural forms needed)`);
      else if (Object.values(v).some((form) => placeholders(form) !== placeholders(other) && placeholders(form) !== placeholders(one))) bad.push(`${k} (placeholders)`);
    } else if (typeof v !== "string" || placeholders(v) !== placeholders(k)) bad.push(`${k} (placeholders)`);
  }
  const extra = Object.keys(dict).filter((k) => !keys.has(k));
  console.log(`${l}: ${sorted.length - missing.length}/${sorted.length} translated, ${bad.length} with problems, ${extra.length} unused`);
  if (write) {
    fs.writeFileSync(path.join(DIR, `missing.${l}.json`), JSON.stringify(missing, null, 1));
  }
  for (const b of bad.slice(0, 10)) console.log(`  ${l}: ${b}`);
  problems += missing.length + bad.length;
}
console.log(`${sorted.length} strings; ${dynamic.length} t() calls with a computed argument (translated if the value is a known string)`);
if (problems && !write) {
  console.error(`${problems} translations missing or wrong. Run: npm run i18n -- --write`);
  process.exit(1);
}
