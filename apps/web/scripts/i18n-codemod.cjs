/* eslint-disable @typescript-eslint/no-require-imports */
/* One-off codemod: wraps the interface's English strings in t(), so every screen can be translated.
   Run once (node scripts/i18n-codemod.cjs [files…]); it reports what it skipped so the rest can be done by hand.

   What it converts:
   - JSX text → {t("…")}, with whitespace and HTML entities normalized;
   - string literals in UI attributes (label, title, placeholder, aria-label, help, subtitle, eyebrow, alt,
     description, caption, note) → attr={t("…")};
   - inside functions only: string and template literals that are JSX children (directly or as either side of
     ?:, ||, ??), values of UI properties ({ label: "…", text: "…", message: "…", word: "…" …}), and the first
     argument of setError/setNotice/setMessage/confirm/alert. Template literals become t("… {name} …", { name }).
   - at module level, string values of UI properties become msg("…") (marked, translated where shown).
*/
const ts = require("typescript");
const fs = require("fs");
const path = require("path");

const UI_ATTRS = new Set(["label", "title", "placeholder", "aria-label", "help", "subtitle", "eyebrow", "alt", "description", "caption", "note", "confirmLabel", "emptyText"]);
const UI_PROPS = new Set(["label", "title", "text", "message", "word", "description", "help", "note", "placeholder", "caption", "heading", "subtitle", "hint", "eyebrow", "body", "tip", "empty", "summary", "detail"]);
const UI_CALLS = new Set(["setError", "setNotice", "confirm", "alert", "setInfo", "setStatusText"]);
const SKIP_FILES = [/app\/widget\//, /components\/chat-form\.tsx$/, /\/i18n\//];
const ENTITIES = { "&apos;": "'", "&quot;": '"', "&amp;": "&", "&nbsp;": " ", "&lt;": "<", "&gt;": ">", "&rsquo;": "’", "&lsquo;": "‘", "&ldquo;": "“", "&rdquo;": "”", "&mdash;": "—", "&ndash;": "–", "&hellip;": "…", "&middot;": "·", "&rarr;": "→" };

const report = [];

function prose(s) {
  if (!/[A-Za-z]/.test(s)) return false;
  if (/^(\/|#|https?:|mailto:|data:|\.\/)/.test(s)) return false;
  if (/^[a-z0-9_.:-]+$/.test(s)) return false; // keys, classes, codes
  if (/^vr-/.test(s)) return false;
  return true;
}

function decode(s) {
  let out = s.replace(/&[a-z]+;|&#\d+;/g, (m) => (m in ENTITIES ? ENTITIES[m] : m.startsWith("&#") ? String.fromCharCode(Number(m.slice(2, -1))) : m));
  return out;
}

function inFunction(n) {
  for (let p = n.parent; p; p = p.parent) {
    if (ts.isFunctionDeclaration(p) || ts.isArrowFunction(p) || ts.isFunctionExpression(p) || ts.isMethodDeclaration(p)) return true;
  }
  return false;
}

function jsxChildContext(n) {
  // literal → (?:|‖|??|parens)* → JsxExpression that is a child (not an attribute)
  let p = n.parent;
  while (p && (ts.isConditionalExpression(p) || ts.isParenthesizedExpression(p) || (ts.isBinaryExpression(p) && [ts.SyntaxKind.BarBarToken, ts.SyntaxKind.QuestionQuestionToken, ts.SyntaxKind.AmpersandAmpersandToken].includes(p.operatorToken.kind)))) {
    if (ts.isConditionalExpression(p) && p.condition === n) return false;
    if (ts.isBinaryExpression(p) && p.operatorToken.kind === ts.SyntaxKind.AmpersandAmpersandToken && p.left === n) return false;
    n = p;
    p = p.parent;
  }
  return p && ts.isJsxExpression(p) && !ts.isJsxAttribute(p.parent) ? "child" : p && ts.isJsxExpression(p) && ts.isJsxAttribute(p.parent) && UI_ATTRS.has(p.parent.name.getText()) ? "attr" : null;
}

function placeholderName(expr, used) {
  let base = "v";
  const txt = expr.getText();
  const m = txt.match(/([A-Za-z_][A-Za-z0-9_]*)\s*(\(\))?\s*$/);
  if (ts.isIdentifier(expr)) base = expr.text;
  else if (ts.isPropertyAccessExpression(expr)) base = expr.name.text;
  else if (ts.isCallExpression(expr) && expr.arguments.length) {
    const a = expr.arguments[0];
    base = ts.isIdentifier(a) ? a.text : ts.isPropertyAccessExpression(a) ? a.name.text : ts.isIdentifier(expr.expression) ? expr.expression.text : "v";
  } else if (m) base = m[1];
  base = base.replace(/[^A-Za-z0-9_]/g, "") || "v";
  let name = base;
  let i = 2;
  while (used.has(name)) name = `${base}${i++}`;
  used.add(name);
  return name;
}

function templateCall(node, sf, T) {
  const used = new Set();
  let key = node.head.text;
  const vars = [];
  for (const span of node.templateSpans) {
    const name = placeholderName(span.expression, used);
    key += `{${name}}` + span.literal.text;
    vars.push(`${name}: ${span.expression.getText(sf)}`);
  }
  if (!prose(key.replace(/\{\w+\}/g, ""))) return null;
  return `${T}(${JSON.stringify(key)}, { ${vars.join(", ")} })`;
}

const MAPS = new Set();
function collectMaps(file) {
  const src = fs.readFileSync(file, "utf8");
  const sf = ts.createSourceFile(file, src, ts.ScriptTarget.Latest, true, file.endsWith(".tsx") ? ts.ScriptKind.TSX : ts.ScriptKind.TS);
  for (const st of sf.statements) {
    if (!ts.isVariableStatement(st)) continue;
    for (const d of st.declarationList.declarations) {
      if (d.initializer && ts.isIdentifier(d.name) && /\bmsg\(/.test(d.initializer.getText(sf))) MAPS.add(d.name.text);
    }
  }
}

function mapRoot(e) {
  while (e && (ts.isElementAccessExpression(e) || ts.isPropertyAccessExpression(e) || ts.isNonNullExpression(e) || ts.isParenthesizedExpression(e))) e = e.expression;
  return e && ts.isIdentifier(e) ? e.text : null;
}

function transform(file) {
  const src = fs.readFileSync(file, "utf8");
  const kind = file.endsWith(".tsx") ? ts.ScriptKind.TSX : ts.ScriptKind.TS;
  const sf = ts.createSourceFile(file, src, ts.ScriptTarget.Latest, true, kind);
  const edits = [];
  // A file that already has its own `t` (often a ticket) imports the translator as `tr`.
  let localT = false;
  (function scan(n) {
    if ((ts.isVariableDeclaration(n) || ts.isParameter(n) || ts.isBindingElement(n)) && n.name && ts.isIdentifier(n.name) && n.name.text === "t") localT = true;
    ts.forEachChild(n, scan);
  })(sf);
  const T = localT ? "tr" : "t";
  let usesT = false;
  let usesMsg = false;

  function add(start, end, text) {
    edits.push({ start, end, text });
  }

  function visit(n) {
    if (ts.isImportDeclaration(n) || ts.isExportDeclaration(n) || ts.isTypeNode(n)) return;
    // Already translated: t("…"), tp(…), msg("…")
    if (ts.isCallExpression(n) && ts.isIdentifier(n.expression) && ["t", "tr", "tp", "msg"].includes(n.expression.text)) return;

    if (ts.isJsxText(n)) {
      const raw = n.getText(sf);
      const trimmed = raw.trim();
      if (trimmed && /[A-Za-z]/.test(trimmed)) {
        const text = decode(trimmed.replace(/\s+/g, " "));
        if (/&[a-z]+;/.test(text)) report.push(`${file}: unknown entity in "${text}"`);
        const start = n.getStart(sf) + raw.indexOf(trimmed);
        add(start, start + trimmed.length, `{${T}(${JSON.stringify(text)})}`);
        usesT = true;
      }
      return;
    }

    if (ts.isJsxAttribute(n) && n.initializer && ts.isStringLiteral(n.initializer) && UI_ATTRS.has(n.name.getText())) {
      const s = n.initializer.text;
      if (prose(s) || /^[A-Z]/.test(s)) {
        add(n.initializer.getStart(sf), n.initializer.getEnd(), `{${T}(${JSON.stringify(s)})}`);
        usesT = true;
      }
      return;
    }

    // A word from a msg() map shown in JSX or a UI attribute: KIND[p.kind], PRIORITY[x].label → t(…)
    if ((ts.isElementAccessExpression(n) || ts.isPropertyAccessExpression(n)) && MAPS.has(mapRoot(n)) && !(n.parent && (ts.isElementAccessExpression(n.parent) || ts.isPropertyAccessExpression(n.parent)) && n.parent.expression === n)) {
      const j = jsxChildContext(n);
      const p = n.parent;
      const uiProp = p && ts.isPropertyAssignment(p) && p.initializer === n && UI_PROPS.has(p.name.getText(sf).replace(/["']/g, "")) && inFunction(n);
      const isCallee = p && ts.isCallExpression(p) && p.expression === n;
      if ((j || uiProp) && !isCallee) {
        add(n.getStart(sf), n.getEnd(), `${T}(${n.getText(sf)})`);
        usesT = true;
        return;
      }
    }
    const isStr = ts.isStringLiteral(n) || ts.isNoSubstitutionTemplateLiteral(n);
    const isTpl = ts.isTemplateExpression(n);
    if (isStr || isTpl) {
      let parent = n.parent;
      let top = n;
      while (parent && (ts.isConditionalExpression(parent) || ts.isParenthesizedExpression(parent) || (ts.isBinaryExpression(parent) && [ts.SyntaxKind.BarBarToken, ts.SyntaxKind.QuestionQuestionToken].includes(parent.operatorToken.kind)))) {
        if (ts.isConditionalExpression(parent) && parent.condition === top) break;
        top = parent;
        parent = parent.parent;
      }
      let ctx = null;
      if (top !== n && parent && ts.isPropertyAssignment(parent) && parent.initializer === top) {
        const name = parent.name.getText(sf).replace(/["']/g, "");
        if (UI_PROPS.has(name) && inFunction(n)) ctx = "prop";
      } else if (top !== n && parent && ts.isCallExpression(parent) && parent.arguments[0] === top && ts.isIdentifier(parent.expression) && UI_CALLS.has(parent.expression.text)) {
        ctx = "call";
      }
      parent = n.parent;
      if (ctx) {
        /* handled below */
      } else if (parent && ts.isPropertyAssignment(parent) && parent.initializer === n) {
        const name = parent.name.getText(sf).replace(/["']/g, "");
        if (UI_PROPS.has(name)) ctx = "prop";
      } else if (parent && ts.isCallExpression(parent) && parent.arguments[0] === n && ts.isIdentifier(parent.expression) && UI_CALLS.has(parent.expression.text)) {
        ctx = "call";
      } else {
        const j = jsxChildContext(n);
        if (j) ctx = j;
      }
      // A map of codes to words at module level ({ chatbot_form: "Chatbot form" }) is UI too.
      if (!ctx && parent && ts.isPropertyAssignment(parent) && parent.initializer === n && !inFunction(n)) {
        const obj = parent.parent;
        if (ts.isObjectLiteralExpression(obj) && obj.properties.every((p) => ts.isPropertyAssignment(p) && (ts.isStringLiteral(p.initializer) || ts.isNoSubstitutionTemplateLiteral(p.initializer))) && isStr && /^[A-Z]/.test(n.text) && /\s|^[A-Z][a-z]+$/.test(n.text)) ctx = "map";
      }
      if (ctx) {
        if (!inFunction(n) && ctx !== "child" && ctx !== "attr") {
          if (isStr && prose(n.text) && /[A-Z]/.test(n.text[0])) {
            add(n.getStart(sf), n.getEnd(), `msg(${JSON.stringify(n.text)})`);
            usesMsg = true;
          }
          return;
        }
        if (isStr && (prose(n.text) || /^[A-Z]/.test(n.text))) {
          add(n.getStart(sf), n.getEnd(), `${T}(${JSON.stringify(n.text)})`);
          usesT = true;
          return;
        }
        if (isTpl) {
          const call = templateCall(n, sf, T);
          if (call) {
            add(n.getStart(sf), n.getEnd(), call);
            usesT = true;
          }
          return;
        }
      }
    }
    ts.forEachChild(n, visit);
  }
  visit(sf);
  if (!edits.length) return 0;
  edits.sort((a, b) => b.start - a.start);
  let out = src;
  for (const e of edits) out = out.slice(0, e.start) + e.text + out.slice(e.end);
  const names = [usesT && (localT ? "t as tr" : "t"), usesMsg && "msg"].filter(Boolean);
  if (names.length && !/from "@\/i18n"/.test(out)) {
    // after "use client" and before the first import
    const m = out.match(/^("use client";\s*\n)?/);
    const at = m ? m[0].length : 0;
    out = out.slice(0, at) + `${m && m[0] ? "\n" : ""}import { ${names.join(", ")} } from "@/i18n";\n` + out.slice(at);
  }
  fs.writeFileSync(file, out);
  return edits.length;
}

function walk(d, out = []) {
  for (const f of fs.readdirSync(d)) {
    const p = path.join(d, f);
    if (fs.statSync(p).isDirectory()) walk(p, out);
    else if (/\.(tsx|ts)$/.test(p)) out.push(p);
  }
  return out;
}

const files = process.argv.slice(2).length ? process.argv.slice(2) : walk("src");
for (const f of walk("src")) collectMaps(f);
let total = 0;
for (const f of files) {
  if (SKIP_FILES.some((r) => r.test(f))) continue;
  const n = transform(f);
  if (n) console.log(`${n}\t${f}`);
  total += n;
}
console.log(`${total} strings wrapped`);
for (const r of report) console.log("SKIPPED", r);
