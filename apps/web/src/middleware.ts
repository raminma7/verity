import { NextResponse, type NextRequest } from "next/server";

/**
 * The chatbot widget page may only be framed by the chatbot's authorized domains. Every other page keeps
 * X-Frame-Options: DENY (next.config.mjs). The API also checks the embedding origin on every widget call.
 */
const apiUrl = process.env.VERITY_API_URL ?? "http://localhost:8000";
const cache = new Map<string, { at: number; ancestors: string[] }>();
const TTL = 30_000;

async function ancestors(key: string): Promise<string[]> {
  const hit = cache.get(key);
  if (hit && Date.now() - hit.at < TTL) return hit.ancestors;
  try {
    const r = await fetch(`${apiUrl}/api/widget/${encodeURIComponent(key)}/frame-policy`, { cache: "no-store" });
    const list = r.ok ? ((await r.json()) as { ancestors: string[] }).ancestors : [];
    const safe = list.filter((a) => /^https?:\/\/(\*\.)?[a-z0-9.-]+(:\*|:\d+)?$/i.test(a));
    cache.set(key, { at: Date.now(), ancestors: safe });
    return safe;
  } catch {
    return [];
  }
}

export async function middleware(req: NextRequest) {
  const key = req.nextUrl.pathname.split("/")[2] ?? "";
  const list = await ancestors(key);
  const res = NextResponse.next();
  res.headers.set("Content-Security-Policy", `frame-ancestors ${list.length ? list.join(" ") : "'none'"}`);
  res.headers.set("Referrer-Policy", "strict-origin-when-cross-origin");
  return res;
}

export const config = { matcher: ["/widget/:key*"] };
