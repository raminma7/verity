// Generates the app icon files from the logo symbol: node scripts/build-icons.mjs  (npm run icons).
// Needs Playwright's Chromium (a dev dependency) to rasterise; the ICO container is written by hand.
// Tile: rounded square (radius 24% of its size) in brand teal, the symbol centred at 56% in the teal-background colours.
import { mkdirSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import { chromium } from "playwright";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const TEAL = "#0d6655";
const LENS = "#d6f35b";

const symbol = (b1, b2) =>
  `<path d="M0 64 A36 36 0 0 1 36 28 A36 36 0 0 1 72 64 A36 36 0 0 1 36 100 L0 100 Z" fill="${b1}"/>` +
  `<path d="M28 36 A36 36 0 0 1 64 0 L100 0 L100 36 A36 36 0 0 1 64 72 A36 36 0 0 1 28 36 Z" fill="${b2}"/>` +
  `<path d="M28.74 28.74 A36 36 0 0 1 71.26 71.26 A36 36 0 0 1 28.74 28.74 Z" fill="${LENS}"/>`;

/** The tile as an SVG of `size` units. radius: corner radius in the same units (0 for full-bleed). */
function tile(size, radius) {
  const s = size * 0.56;
  const o = (size - s) / 2;
  return (
    `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${size} ${size}" width="${size}" height="${size}">` +
    `<rect width="${size}" height="${size}" rx="${radius}" fill="${TEAL}"/>` +
    `<svg x="${o}" y="${o}" width="${s}" height="${s}" viewBox="0 0 100 100">${symbol("#f5f3ed", "#15171a")}</svg></svg>`
  );
}
const plain = (size, b1, b2) => `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100" width="${size}" height="${size}">${symbol(b1, b2)}</svg>`;

const browser = await chromium.launch();
async function png(svg, size) {
  const page = await browser.newPage({ viewport: { width: size, height: size }, deviceScaleFactor: 1 });
  await page.setContent(`<style>html,body{margin:0;background:transparent}svg{display:block}</style>${svg}`);
  const buf = await page.screenshot({ omitBackground: true, clip: { x: 0, y: 0, width: size, height: size } });
  await page.close();
  return buf;
}

function ico(images) {
  const head = Buffer.alloc(6 + 16 * images.length);
  head.writeUInt16LE(1, 2);
  head.writeUInt16LE(images.length, 4);
  let offset = head.length;
  images.forEach(({ size, data }, i) => {
    const e = 6 + 16 * i;
    head[e] = size === 256 ? 0 : size;
    head[e + 1] = size === 256 ? 0 : size;
    head.writeUInt16LE(1, e + 4);
    head.writeUInt16LE(32, e + 6);
    head.writeUInt32LE(data.length, e + 8);
    head.writeUInt32LE(offset, e + 12);
    offset += data.length;
  });
  return Buffer.concat([head, ...images.map((i) => i.data)]);
}

const out = (p, data) => {
  const f = join(root, p);
  mkdirSync(dirname(f), { recursive: true });
  writeFileSync(f, data);
  console.log("wrote", p);
};

// favicon.svg and favicon.ico (the 16px image uses a 3px corner radius), the iOS icon, and the PWA icons.
out("src/app/icon.svg", tile(100, 24) + "\n");
out("src/app/favicon.ico", ico(await Promise.all([16, 32, 48].map(async (size) => ({ size, data: await png(tile(size, size === 16 ? 3 : size * 0.24), size) })))));
out("src/app/apple-icon.png", await png(tile(180, 0), 180)); // full-bleed: iOS rounds it itself
for (const size of [192, 512]) {
  out(`public/brand/icon-${size}.png`, await png(tile(size, size * 0.24), size));
  out(`public/brand/icon-maskable-${size}.png`, await png(tile(size, 0), size)); // full-bleed; the symbol sits inside the safe zone
}
out("public/brand/logo-symbol.png", await png(plain(104, "#0d6655", "#15171a"), 104)); // for email, shown at 26px
await browser.close();
