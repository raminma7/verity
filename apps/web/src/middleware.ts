import { NextResponse, type NextRequest } from "next/server";

/**
 * Two jobs.
 *
 * The chatbot widget page may only be framed by the chatbot's authorized domains. Every other page keeps
 * X-Frame-Options: DENY (next.config.mjs). The API also checks the embedding origin on every widget call.
 *
 * The public pages (the landing page and Privacy) exist in six languages: English at "/" and "/privacy", the
 * others under "/es", "/de", "/fr", "/it", "/ar". English is rewritten to the "/en" route (the address stays),
 * "/en" itself redirects to "/" so each page has one address, and the language is passed on in a header so the
 * root layout can set <html lang dir> on the server. A signed-in visitor on "/" goes to the dashboard.
 */
const apiUrl = process.env.VERITY_API_URL ?? "http://localhost:8000";
const cache = new Map<string, { at: number; ancestors: string[] }>();
const TTL = 30_000;

async function ancestors(key: string): Promise<string[]> {
  const hit = cache.get(key);
  if (hit && Date.now() - hit.at < TTL) return hit.ancestors;
  try {
    const r = await fetch(`${apiUrl}/api/widget/${encodeURIComponent(key)}/frame-policy`, { cache: "no-store" });
    if (!r.ok) throw new Error(`frame-policy answered ${r.status}`);
    const list = ((await r.json()) as { ancestors: string[] }).ancestors;
    const safe = list.filter((a) => /^https?:\/\/(\*\.)?[a-z0-9.-]+(:\*|:\d+)?$/i.test(a));
    cache.set(key, { at: Date.now(), ancestors: safe });
    return safe;
  } catch (e) {
    // Not cached, so the next request tries again; the widget stays unframable meanwhile. Say why, as this is a setup problem.
    console.error(`widget frame policy: could not read ${apiUrl}/api/widget/…/frame-policy: ${e instanceof Error ? e.message : e}`);
    return [];
  }
}

async function widget(req: NextRequest) {
  const key = req.nextUrl.pathname.split("/")[2] ?? "";
  const list = await ancestors(key);
  const res = NextResponse.next();
  res.headers.set("Content-Security-Policy", `frame-ancestors ${list.length ? list.join(" ") : "'none'"}`);
  res.headers.set("Referrer-Policy", "strict-origin-when-cross-origin");
  return res;
}

const OTHER = ["es", "de", "fr", "it", "ar"];
const SESSION_COOKIE = process.env.VERITY_SESSION_COOKIE ?? "verity_session";

function site(req: NextRequest) {
  const { pathname } = req.nextUrl;
  const [first, ...rest] = pathname.split("/").filter(Boolean);
  const tail = rest.length ? `/${rest.join("/")}` : "";

  if (first === "en") {
    const url = req.nextUrl.clone();
    url.pathname = tail || "/";
    return NextResponse.redirect(url, 308);
  }
  if (!first && req.cookies.has(SESSION_COOKIE)) {
    const url = req.nextUrl.clone();
    url.pathname = "/dashboard";
    url.search = "";
    return NextResponse.redirect(url);
  }
  const headers = new Headers(req.headers);
  if (first && OTHER.includes(first)) {
    headers.set("x-verity-locale", first);
    return NextResponse.next({ request: { headers } });
  }
  headers.set("x-verity-locale", "en");
  const url = req.nextUrl.clone();
  url.pathname = `/en${first ? `/${first}${tail}` : ""}`;
  return NextResponse.rewrite(url, { request: { headers } });
}

export async function middleware(req: NextRequest) {
  return req.nextUrl.pathname.startsWith("/widget/") ? widget(req) : site(req);
}

export const config = { matcher: ["/", "/privacy", "/(en|es|de|fr|it|ar)", "/(en|es|de|fr|it|ar)/privacy", "/widget/:key*"] };
