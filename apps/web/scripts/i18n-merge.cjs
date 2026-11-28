/* eslint-disable @typescript-eslint/no-require-imports */
/* Builds src/i18n/locales/<lang>.json from numbered translation files (one "N<TAB>text" per line, numbered as
   the English strings in en.json; plurals as "one:…|other:…|few:…"). Used while translating; the dictionaries
   are what ships. node scripts/i18n-merge.cjs <dir-with-lang-subfolders> */
const fs = require("fs");
const path = require("path");

const dir = process.argv[2];
const en = Object.entries(JSON.parse(fs.readFileSync(path.join(__dirname, "..", "src", "i18n", "en.json"), "utf8")));
for (const lang of fs.readdirSync(dir)) {
  const out = {};
  for (const f of fs.readdirSync(path.join(dir, lang)).sort()) {
    for (const line of fs.readFileSync(path.join(dir, lang, f), "utf8").split("\n")) {
      if (!line.trim()) continue;
      const tab = line.indexOf("\t");
      const n = Number(line.slice(0, tab));
      const text = line.slice(tab + 1).replace(/\\n/g, "\n");
      const [key, kind] = en[n] ?? [];
      if (key === undefined) throw new Error(`${lang}/${f}: no English string ${n}`);
      if (kind === "plural") {
        out[key] = Object.fromEntries(text.split("|").map((p) => [p.slice(0, p.indexOf(":")), p.slice(p.indexOf(":") + 1)]));
      } else out[key] = text;
    }
  }
  const file = path.join(__dirname, "..", "src", "i18n", "locales", `${lang}.json`);
  fs.writeFileSync(file, JSON.stringify(out, null, 1) + "\n");
  console.log(`${lang}: ${Object.keys(out).length} strings`);
}
