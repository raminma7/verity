import type { MetadataRoute } from "next";

import tokens from "@/design/tokens.json";

type Token = { name: string; value: { light: string; dark: string } };
const colour = (name: string) => (tokens.color.tokens as Token[]).find((t) => t.name === name)!.value.light;

export default function manifest(): MetadataRoute.Manifest {
  return {
    name: "Verity",
    short_name: "Verity",
    description: "AI customer support: knowledge base, chatbot and ticketing in one workspace.",
    start_url: "/dashboard",
    display: "standalone",
    background_color: colour("ground"),
    theme_color: colour("brand"),
    icons: [
      { src: "/brand/icon-192.png", sizes: "192x192", type: "image/png", purpose: "any" },
      { src: "/brand/icon-512.png", sizes: "512x512", type: "image/png", purpose: "any" },
      { src: "/brand/icon-maskable-192.png", sizes: "192x192", type: "image/png", purpose: "maskable" },
      { src: "/brand/icon-maskable-512.png", sizes: "512x512", type: "image/png", purpose: "maskable" },
    ],
  };
}
