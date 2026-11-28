import { expect, test, type Page } from "@playwright/test";

/**
 * The public landing page: six languages (server-rendered lang and dir, hreflang, metadata), the plans from the API,
 * the calls to action, the privacy page, sitemap and robots, signed-in visitors sent to the dashboard, the keyboard,
 * and phone width. Run against the API and the production web build, as in CI.
 */
const SHOTS = process.env.E2E_SHOTS;
const PASSWORD = "Correct-horse-42";
const ORIGIN = { Origin: process.env.E2E_BASE_URL ?? "http://localhost:3000" };

const LANGS = [
  { code: "en", path: "/", dir: "ltr", h1: "One workspace for the whole support loop." },
  { code: "es", path: "/es", dir: "ltr", h1: "Un solo espacio de trabajo para todo el ciclo de soporte." },
  { code: "de", path: "/de", dir: "ltr", h1: "Ein Workspace für den ganzen Support-Kreislauf." },
  { code: "fr", path: "/fr", dir: "ltr", h1: "Un seul espace de travail pour toute la boucle du support." },
  { code: "it", path: "/it", dir: "ltr", h1: "Un solo workspace per l’intero ciclo del supporto." },
  { code: "ar", path: "/ar", dir: "rtl", h1: "مساحة عمل واحدة لدورة الدعم كلها." },
] as const;

async function shot(page: Page, name: string) {
  if (SHOTS) await page.screenshot({ path: `${SHOTS}/${name}.png`, fullPage: true });
}

for (const l of LANGS) {
  test(`${l.code}: server-rendered language, direction, metadata and hreflang`, async ({ page, request }) => {
    // The raw HTML already has the language: nothing waits for JavaScript.
    const raw = await (await request.get(l.path)).text();
    expect(raw).toContain(`<html lang="${l.code}" dir="${l.dir}"`);
    expect(raw).toContain(l.h1);

    await page.goto(l.path);
    await expect(page.locator("html")).toHaveAttribute("lang", l.code);
    await expect(page.locator("html")).toHaveAttribute("dir", l.dir);
    await expect(page.getByRole("heading", { level: 1 })).toHaveText(l.h1);
    await expect(page.getByRole("heading", { level: 1 })).toHaveCount(1);
    await expect(page.locator("main")).toHaveCount(1);
    expect((await page.title()).length).toBeGreaterThan(10);
    await expect(page.locator('meta[name="description"]')).toHaveAttribute("content", /.{40,}/);
    await expect(page.locator('meta[property="og:title"]')).toHaveCount(1);
    await expect(page.locator('meta[property="og:locale"]')).toHaveCount(1);

    const canonical = new URL((await page.locator('link[rel="canonical"]').getAttribute("href"))!);
    expect(canonical.pathname).toBe(l.code === "en" ? "/" : l.path);
    const alt = await page.locator('link[rel="alternate"][hreflang]').evaluateAll((els) => els.map((e) => [e.getAttribute("hreflang"), new URL(e.getAttribute("href")!).pathname]));
    expect(Object.fromEntries(alt)).toEqual({ en: "/", es: "/es", de: "/de", fr: "/fr", it: "/it", ar: "/ar", "x-default": "/" });
  });
}

test("each language has its own title, and English lives at / only", async ({ page, request }) => {
  const titles = new Set<string>();
  for (const l of LANGS) {
    await page.goto(l.path);
    titles.add(await page.title());
  }
  expect(titles.size).toBe(6);
  const r = await request.get("/en", { maxRedirects: 0 });
  expect(r.status()).toBe(308);
  expect(new URL(r.headers().location, "http://x").pathname).toBe("/");
  expect((await request.get("/zz")).status()).toBe(404);
});

test("Arabic reads right to left, with the page laid out from the right", async ({ page }) => {
  await page.goto("/ar");
  const h1 = page.getByRole("heading", { level: 1 });
  expect(await h1.evaluate((e) => getComputedStyle(e).direction)).toBe("rtl");
  const box = (await h1.boundingBox())!;
  const vw = page.viewportSize()!.width;
  expect(box.x + box.width / 2).toBeGreaterThan(vw / 2); // the copy sits on the right, the sample on the left
  const logo = (await page.locator(".vr-land-head .vr-logo-link").boundingBox())!;
  expect(logo.x).toBeGreaterThan(vw / 2);
  // Arabic samples on every page keep their own direction, and the code line stays left to right.
  await page.goto("/");
  await expect(page.locator('[lang="ar"][dir="rtl"]').first()).toBeVisible();
  await expect(page.locator("pre.vr-land-code")).toHaveAttribute("dir", "ltr");
});

test("plans and prices come from the API, formatted in the page's language", async ({ page, request }) => {
  const plans = (await (await request.get("/api/plans")).json()) as { name: string; price_cents: number; currency: string; billing_cycle: string; token_allowance: number; limits: Record<string, number> }[];
  expect(plans.length).toBeGreaterThan(0);
  const tag = { en: "en-US", es: "es", de: "de", fr: "fr", it: "it", ar: "ar-u-nu-latn" } as const;
  for (const l of LANGS) {
    await page.goto(l.path);
    const section = page.locator("#pricing");
    await expect(section.locator("article.vr-plan")).toHaveCount(plans.length);
    const text = (await section.innerText()).replace(/ | /g, " ");
    for (const p of plans) {
      const price = new Intl.NumberFormat(tag[l.code], { style: "currency", currency: p.currency, currencyDisplay: "narrowSymbol", minimumFractionDigits: p.price_cents % 100 ? 2 : 0 }).format(p.price_cents / 100);
      expect(text).toContain(p.name);
      expect(text).toContain(price.replace(/ | /g, " "));
    }
  }
  // Every card shows the plan's own limits, straight from the API (-1 is unlimited).
  await page.goto("/");
  for (const p of plans) {
    const card = page.locator("article.vr-plan", { hasText: p.name }).first();
    await expect(card.locator("dl")).toContainText(p.limits.members < 0 ? "Unlimited" : String(p.limits.members));
  }
  await shot(page, "landing-pricing");
});

test("the calls to action lead to sign-up and sign-in", async ({ page }) => {
  await page.goto("/");
  const create = page.getByRole("link", { name: "Create your workspace" });
  expect(await create.count()).toBeGreaterThanOrEqual(4); // header, hero, plans, closing, footer
  for (const link of await create.all()) await expect(link).toHaveAttribute("href", "/sign-up");
  for (const link of await page.getByRole("link", { name: "Sign in", exact: true }).all()) await expect(link).toHaveAttribute("href", "/sign-in");
  await page.getByRole("banner").getByRole("link", { name: "Create your workspace" }).click();
  await expect(page).toHaveURL(/\/sign-up$/);
  await page.goto("/es");
  await page.getByRole("banner").getByRole("link", { name: "Iniciar sesión" }).click();
  await expect(page).toHaveURL(/\/sign-in$/);
});

test("the language menu and footer link to every language", async ({ page }) => {
  await page.goto("/");
  await page.locator(".vr-land-lang summary").click();
  await page.locator(".vr-land-lang").getByRole("link", { name: "Deutsch" }).click();
  await expect(page).toHaveURL(/\/de$/);
  await expect(page.locator("html")).toHaveAttribute("lang", "de");
  const links = await page.locator("footer nav[aria-label] a[hreflang]").evaluateAll((els) => els.map((e) => e.getAttribute("hreflang")));
  expect(links).toEqual(["en", "es", "de", "fr", "it", "ar"]);
});

async function verifiedUser(page: Page, email: string) {
  const r = await page.request.post("/api/auth/register", { data: { full_name: "Landing Visitor", email, password: PASSWORD }, headers: ORIGIN });
  expect(r.status()).toBe(202);
  for (let i = 0; i < 80; i++) {
    const mails = (await (await page.request.get(`/api/dev/outbox?to=${encodeURIComponent(email)}`)).json()) as { links: string[] }[];
    const found = mails.flatMap((m) => m.links).find((x) => x.includes("/verify-email"));
    if (found) {
      await page.goto(new URL(found).pathname + new URL(found).search);
      return;
    }
    await page.waitForTimeout(250);
  }
  throw new Error("no verification email");
}

test("a signed-in visitor on / goes to the dashboard; a visitor stays on the landing page", async ({ page, browser }) => {
  const anon = await browser.newContext();
  const visitor = await anon.newPage();
  await visitor.goto("/");
  await expect(visitor).toHaveURL(/\/$/);
  await expect(visitor.getByRole("heading", { level: 1 })).toBeVisible();
  await anon.close();

  await verifiedUser(page, `landing-${Date.now()}@example.com`);
  await expect(page).toHaveURL(/dashboard/);
  const t0 = Date.now();
  await page.goto("/");
  await expect(page).toHaveURL(/dashboard/);
  expect(Date.now() - t0).toBeLessThan(10_000);
  // The other languages stay public for a signed-in person.
  await page.goto("/es");
  await expect(page).toHaveURL(/\/es$/);
});

test("the privacy page is in every language and says it is a draft", async ({ page }) => {
  for (const l of LANGS) {
    await page.goto(l.code === "en" ? "/privacy" : `/${l.code}/privacy`);
    await expect(page.locator("html")).toHaveAttribute("lang", l.code);
    await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
    await expect(page.locator('meta[name="robots"]')).toHaveAttribute("content", /noindex/);
    await expect(page.locator(".vr-banner")).toBeVisible();
  }
});

test("sitemap.xml lists every language with alternates, and robots.txt keeps the app out", async ({ request }) => {
  const sitemap = await (await request.get("/sitemap.xml")).text();
  for (const p of ["/es", "/de", "/fr", "/it", "/ar", "/privacy", "/es/privacy"]) expect(sitemap).toMatch(new RegExp(`<loc>[^<]*${p}</loc>`));
  expect(sitemap).toContain('hreflang="ar"');
  expect(sitemap).toContain('hreflang="x-default"');
  const robots = await (await request.get("/robots.txt")).text();
  expect(robots).toMatch(/Sitemap: .*\/sitemap\.xml/);
  for (const p of ["/dashboard", "/api/", "/widget/", "/admin"]) expect(robots).toContain(`Disallow: ${p}`);
});

test("the contact form is shown only when the API has a contact address", async ({ page, request }) => {
  const enabled = (await request.get("/api/public/contact")).status() === 200;
  await page.goto("/");
  await expect(page.locator("#contact")).toHaveCount(enabled ? 1 : 0);
  expect((await request.post("/api/public/contact", { data: { name: "A", email: "a@example.org", message: "Hi" }, headers: ORIGIN })).status()).toBe(enabled ? 202 : 404);
});

test("the keyboard reaches the content, the FAQ and the language menu", async ({ page }) => {
  await page.goto("/");
  await page.keyboard.press("Tab");
  await expect(page.getByRole("link", { name: "Skip to main content" })).toBeFocused();
  await page.keyboard.press("Enter");
  await expect(page).toHaveURL(/#main$/);
  const first = page.locator("#faq summary").first();
  await first.focus();
  await page.keyboard.press("Enter");
  await expect(page.locator("#faq details").first()).toHaveAttribute("open", "");
  await expect(page.locator("#faq details").first().getByText(/offers to bring in a person/)).toBeVisible();
  await page.keyboard.press("Enter");
  await expect(page.locator("#faq details").first()).not.toHaveAttribute("open", "");
  const sections = page.locator("main h2");
  expect(await sections.count()).toBeGreaterThanOrEqual(10);
});

test("a citation selects the passage it points to, and status always has a word", async ({ page }) => {
  await page.goto("/");
  const hero = page.locator(".vr-land-hero");
  const cite2 = hero.getByRole("button", { name: /Source 2/ });
  await cite2.click();
  await expect(hero.locator(".vr-source").nth(1)).toHaveClass(/is-active/);
  await expect(hero.locator(".vr-source").nth(0)).not.toHaveClass(/is-active/);
  await expect(hero.getByText("High confidence")).toBeVisible();
  await expect(page.getByText("No sources found", { exact: true })).toBeVisible();
  await expect(page.getByText("Possible spam", { exact: true })).toBeVisible();
  // Every mock says it is a sample.
  expect(await page.getByText("Sample", { exact: true }).count()).toBeGreaterThanOrEqual(8);
});

test("the sample form needs a click to confirm and sends nothing", async ({ page }) => {
  const sent: string[] = [];
  page.on("request", (r) => r.method() === "POST" && sent.push(r.url()));
  await page.goto("/");
  const form = page.locator(".vr-land-form");
  await expect(form.getByLabel("Name")).toHaveValue("Sam Rivera");
  await form.getByRole("button", { name: "Confirm and send" }).click();
  await expect(form.getByText("This is a sample: nothing was sent.")).toBeVisible();
  expect(sent).toEqual([]);
});

for (const l of [LANGS[0], LANGS[5], LANGS[2]]) {
  test(`${l.code}: nothing scrolls sideways at phone width`, async ({ browser }) => {
    const ctx = await browser.newContext({ viewport: { width: 390, height: 844 } });
    const page = await ctx.newPage();
    await page.goto(l.path);
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);
    await shot(page, `landing-${l.code}-phone`);
    await ctx.close();
  });
}

test("screenshots: light, dark and Arabic", async ({ page }) => {
  test.skip(!SHOTS, "set E2E_SHOTS to a folder to take them");
  await page.goto("/");
  await shot(page, "landing-en-light");
  await page.emulateMedia({ colorScheme: "dark" });
  await shot(page, "landing-en-dark");
  await page.goto("/ar");
  await shot(page, "landing-ar-dark");
  await page.emulateMedia({ colorScheme: "light" });
  await shot(page, "landing-ar-light");
});
