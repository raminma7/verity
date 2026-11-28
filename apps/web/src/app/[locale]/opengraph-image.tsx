import { readFile } from "node:fs/promises";
import { join } from "node:path";

import { ImageResponse } from "next/og";

import tokens from "@/design/tokens.json";

export const alt = "Verity";
export const size = { width: 1200, height: 630 };
export const contentType = "image/png";

type Token = { name: string; value: { light: string; dark: string } };
const colour = (name: string) => (tokens.color.tokens as Token[]).find((t) => t.name === name)!.value.light;

/** The logo on paper, from the design tokens: the symbol (inline SVG) and the Geist 600 wordmark. No translated text. */
export default async function Image() {
  const font = await readFile(join(process.cwd(), "src/assets/fonts/Geist-SemiBold.ttf"));
  const [b1, b2, lens] = [colour("logo-bubble-1"), colour("logo-bubble-2"), colour("highlight")];
  const word = 200;
  return new ImageResponse(
    (
      <div style={{ width: "100%", height: "100%", display: "flex", alignItems: "center", justifyContent: "center", background: colour("ground") }}>
        <div style={{ display: "flex", alignItems: "center", gap: word * 0.23 }}>
          <svg width={word * 0.92} height={word * 0.92} viewBox="0 0 100 100">
            <path d="M0 64 A36 36 0 0 1 36 28 A36 36 0 0 1 72 64 A36 36 0 0 1 36 100 L0 100 Z" fill={b1} />
            <path d="M28 36 A36 36 0 0 1 64 0 L100 0 L100 36 A36 36 0 0 1 64 72 A36 36 0 0 1 28 36 Z" fill={b2} />
            <path d="M28.74 28.74 A36 36 0 0 1 71.26 71.26 A36 36 0 0 1 28.74 28.74 Z" fill={lens} />
          </svg>
          <div style={{ fontFamily: "Geist", fontWeight: 600, fontSize: word, lineHeight: 1, letterSpacing: "-0.045em", color: colour("logo-wordmark") }}>Verity</div>
        </div>
      </div>
    ),
    { ...size, fonts: [{ name: "Geist", data: font, weight: 600, style: "normal" }] },
  );
}
