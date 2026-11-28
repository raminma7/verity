import { ImageResponse } from "next/og";

import tokens from "@/design/tokens.json";

export const alt = "Verity";
export const size = { width: 1200, height: 630 };
export const contentType = "image/png";

type Token = { name: string; value: { light: string; dark: string } };
const colour = (name: string) => (tokens.color.tokens as Token[]).find((t) => t.name === name)!.value.light;

/** A wordmark card made from the design tokens (no text that would need translating, no font to load). */
export default function Image() {
  const ground = colour("ground");
  const ink = colour("ink");
  const highlight = colour("highlight");
  const line = colour("line-strong");
  return new ImageResponse(
    (
      <div style={{ width: "100%", height: "100%", display: "flex", alignItems: "center", justifyContent: "center", background: ground, color: ink }}>
        <div style={{ display: "flex", alignItems: "baseline", fontSize: 220, fontFamily: "serif", letterSpacing: -4 }}>
          Verity
          <div style={{ width: 40, height: 40, marginLeft: 24, borderRadius: 8, background: highlight, border: `3px solid ${line}` }} />
        </div>
      </div>
    ),
    size,
  );
}
