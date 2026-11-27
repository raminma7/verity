import http from "node:http";

import { expect, test, type Page } from "@playwright/test";

/**
 * Phase 5A: create an API tool with a bearer token → test it ("Succeeded") → a tool on a private network is
 * refused → turn it on for a chatbot → a visitor asks where their order is and the answer comes from the API →
 * "How it was answered" shows the call. Routing: a team and a rule → tickets go to the least busy available
 * member (manual and email tickets), someone away is skipped.
 * Run against an API and worker with VERITY_TOOL_TRANSPORT=fake, VERITY_MAIL_TRANSPORT=fake and the fake AI providers.
 */
const SHOTS = process.env.E2E_SHOTS;
const PASSWORD = "Correct-horse-42";
const ORIGIN = { Origin: process.env.E2E_BASE_URL ?? "http://localhost:3000" };

async function shot(page: Page, name: string) {
  if (SHOTS) await page.screenshot({ path: `${SHOTS}/${name}.png`, fullPage: true });
}

async function nav(page: Page, name: string) {
  await page.getByRole("navigation", { name: "Main" }).getByRole("link", { name, exact: true }).click();
}

async function link(page: Page, to: string, path: string): Promise<string> {
  for (let i = 0; i < 40; i++) {
    const mails = (await (await page.request.get(`/api/dev/outbox?to=${encodeURIComponent(to)}`)).json()) as { links: string[] }[];
    const found = mails.flatMap((m) => m.links).find((l) => l.includes(path));
    if (found) return new URL(found).pathname + new URL(found).search;
    await page.waitForTimeout(250);
  }
  throw new Error(`no ${path} email for ${to}`);
}

async function ownerWithPlan(page: Page, email: string) {
  const r = await page.request.post("/api/auth/register", { data: { full_name: "Ramin Example", email, password: PASSWORD }, headers: ORIGIN });
  expect(r.status()).toBe(202);
  await page.goto(await link(page, email, "/verify-email"));
  await expect(page).toHaveURL(/dashboard/);
  const plans = (await (await page.request.get("/api/plans")).json()) as { id: string; key: string }[];
  const co = await (await page.request.post("/api/billing/checkout", { data: { plan_id: plans.find((p) => p.key === "starter")!.id }, headers: ORIGIN })).json();
  expect((await page.request.post(`/api/billing/checkout/${co.checkout_id}/confirm`, { headers: ORIGIN })).ok()).toBeTruthy();
  await page.reload();
}

test("API tools and ticket routing", async ({ page, browser }) => {
  test.setTimeout(240_000);
  const run = Date.now();
  const ownerEmail = `tools-${run}@example.com`;
  await ownerWithPlan(page, ownerEmail);

  // ---- An API tool, with a bearer token ---------------------------------------------------------------------
  await nav(page, "API tools");
  await expect(page.getByText("No API tools yet")).toBeVisible();
  await page.getByRole("link", { name: "New API tool" }).first().click();
  await expect(page.getByRole("heading", { name: "New API tool" })).toBeVisible();
  await page.getByRole("textbox", { name: "Address" }).fill("https://shop.example.com/orders/{order_id}");
  await expect(page.getByText("Add a path parameter for {order_id}.")).toBeVisible();
  await page.getByRole("button", { name: "Add them" }).click();
  await expect(page.getByText("Placeholders match")).toBeVisible();
  await page.getByRole("textbox", { name: "What it is" }).fill("The customer's order number");
  await page.getByLabel("Token").fill(`sk_test_${run}`);
  await page.getByRole("button", { name: "Create tool" }).click();
  await expect(page).toHaveURL(/\/tools\/[0-9a-f-]{36}$/);
  await expect(page.getByText("Saved · encrypted")).toBeVisible();
  await expect(page.locator("body")).not.toContainText(`sk_test_${run}`);
  await expect(page.getByText("Draft", { exact: true })).toBeVisible();

  // Test it: the real (fake) API answers, with the token injected by Verity.
  const testCard = page.getByLabel("Test this tool");
  await testCard.getByRole("textbox", { name: "order_id" }).fill("12345");
  await testCard.getByRole("button", { name: "Run test" }).click();
  const result = page.getByLabel("Test result");
  await expect(result.getByText("Succeeded · 200")).toBeVisible();
  await expect(result).toContainText("shipped");
  await shot(page, "p5a-01-tool-test");
  await page.getByRole("button", { name: "Turn on" }).click();
  await expect(page.getByText("The tool is on.")).toBeVisible();
  await expect(page.getByText("Active", { exact: true })).toBeVisible();

  // A tool whose host resolves to a private network is refused when it's called.
  const internal = await page.request.post("/api/tools", {
    data: {
      name: "inventory",
      description: "Look up how many items of a product are in stock.",
      url_template: "https://db.internal.example/stock",
      auth_type: "none",
    },
    headers: ORIGIN,
  });
  expect(internal.status()).toBe(201);
  const internalId = ((await internal.json()) as { id: string }).id;
  await page.goto(`/tools/${internalId}`);
  await page.getByLabel("Test this tool").getByRole("button", { name: "Run test" }).click();
  await expect(page.getByLabel("Test result").getByText("Failed · Blocked")).toBeVisible();
  await expect(page.getByLabel("Test result")).toContainText("private network");

  // ---- The chatbot uses it ------------------------------------------------------------------------------------
  const bot = (await (await page.request.post("/api/chatbots", { data: { name: "Acme Help" }, headers: ORIGIN })).json()) as { id: string };
  await page.request.patch(`/api/chatbots/${bot.id}`, { data: { allowed_domains: ["localhost"], welcome_message: "Hi! How can we help?" }, headers: ORIGIN });
  await nav(page, "Chatbots");
  await page.goto(`/chatbots/${bot.id}`);
  await page.getByRole("tab", { name: "API tools" }).click();
  await page.getByRole("switch", { name: "Order status" }).click();
  await expect(page.getByRole("switch", { name: "Order status" })).toHaveAttribute("aria-checked", "true");
  await shot(page, "p5a-02-chatbot-tools");

  const snippet = (await (await page.request.get(`/api/chatbots/${bot.id}`)).json()).snippet as string;
  const html = `<!doctype html><html><head><title>Acme shop</title></head><body style="font-family:sans-serif;padding:40px"><h1>Acme shop</h1>${snippet}</body></html>`;
  const server = http.createServer((_, res) => res.writeHead(200, { "content-type": "text/html" }).end(html));
  await new Promise<void>((ok) => server.listen(4102, "127.0.0.1", ok));
  try {
    const ctx = await browser.newContext({ viewport: { width: 1280, height: 900 } });
    const vp = await ctx.newPage();
    await vp.goto("http://localhost:4102/");
    const w = vp.frameLocator("iframe[data-verity]");
    await w.getByRole("button", { name: "Ask us" }).click();
    await expect(w.getByText("Hi! How can we help?")).toBeVisible();
    await w.getByRole("textbox", { name: "Type a message…" }).fill("Where is my order 12345?");
    await w.getByRole("button", { name: "Send" }).last().click();
    await expect(w.getByText(/shipped/).last()).toBeVisible({ timeout: 20_000 });
    await expect(w.getByText(/order_status/)).toHaveCount(0);
    await shot(vp, "p5a-03-widget-answer");
    await ctx.close();
  } finally {
    server.close();
  }
  await nav(page, "Conversations");
  await page.getByRole("link", { name: /Where is my order 12345/ }).first().click();
  await page.getByRole("tab", { name: "How it was answered" }).last().click();
  await expect(page.getByText("Call API · order_status")).toBeVisible();
  await expect(page.getByText(/GET shop\.example\.com\/orders\/\{order_id\} · 200/)).toBeVisible();
  await shot(page, "p5a-04-trace");
  await page.goto("/tools");
  await expect(page.getByText("Used by Acme Help")).toBeVisible();

  // ---- Routing ---------------------------------------------------------------------------------------------------
  // A Support Agent joins.
  const agentEmail = `agent-${run}@example.com`;
  const roles = (await (await page.request.get("/api/workspace/roles")).json()) as { key: string; id: string }[];
  const inv = await page.request.post("/api/workspace/invitations", { data: { email: agentEmail, role_id: roles.find((r) => r.key === "agent")!.id }, headers: ORIGIN });
  expect(inv.status()).toBe(201);
  const agentCtx = await browser.newContext();
  const agent = agentCtx.request;
  expect((await agent.post("/api/auth/register", { data: { full_name: "Sam Agent", email: agentEmail, password: PASSWORD }, headers: ORIGIN })).status()).toBe(202);
  const verifyToken = new URLSearchParams((await link(page, agentEmail, "/verify-email")).split("?")[1]).get("token");
  expect((await agent.post("/api/auth/verify-email", { data: { token: verifyToken }, headers: ORIGIN })).ok()).toBeTruthy();
  const inviteToken = (await link(page, agentEmail, "/invite/")).split("/invite/")[1];
  expect((await agent.post(`/api/invitations/${inviteToken}/accept`, { headers: ORIGIN })).ok()).toBeTruthy();

  const billing = (await (await page.request.post("/api/ticket-categories", { data: { name: "Billing", description: "Invoices, refunds and payments" }, headers: ORIGIN })).json()) as { id: string };

  await nav(page, "Settings");
  await page.getByRole("navigation", { name: "Settings sections" }).getByRole("link", { name: "Routing" }).click();
  await page.getByRole("switch", { name: "Assign new tickets automatically" }).click();
  await page.getByRole("button", { name: "Save", exact: true }).first().click();
  await expect(page.getByText("Routing settings saved.")).toBeVisible();
  await page.getByRole("textbox", { name: "New team" }).fill("Billing team");
  await page.getByRole("button", { name: "Add team" }).click();
  await expect(page.getByText("Billing team added.")).toBeVisible();
  const team = page.getByLabel("Teams");
  await team.getByRole("group", { name: "Members" }).getByRole("button", { name: "Sam Agent" }).click();
  await expect(team.getByRole("group", { name: "Members" }).getByRole("button", { name: "Sam Agent" })).toHaveAttribute("aria-pressed", "true");
  await team.getByRole("group", { name: "Members" }).getByRole("button", { name: "Ramin Example" }).click();
  await expect(team.getByRole("group", { name: "Members" }).getByRole("button", { name: "Ramin Example" })).toHaveAttribute("aria-pressed", "true");
  const ruleForm = page.getByRole("form", { name: "Add a rule" });
  await ruleForm.getByRole("textbox", { name: "Name" }).fill("Billing and email");
  await ruleForm.getByRole("group", { name: "Category" }).getByRole("button", { name: "Billing" }).click();
  await ruleForm.getByLabel("Team").selectOption({ label: "Billing team" });
  await ruleForm.getByRole("button", { name: "Add rule" }).click();
  await expect(page.getByText("Rule added.")).toBeVisible();
  await expect(page.getByLabel("Routing rules")).toContainText("Tickets in Billing → team “Billing team”");
  const emailRule = await page.request.post("/api/routing/rules", {
    data: { name: "Email", conditions: { sources: ["email"] }, action: { team_id: ((await (await page.request.get("/api/teams")).json()) as { items: { id: string }[] }).items[0].id } },
    headers: ORIGIN,
  });
  expect(emailRule.status()).toBe(201);
  await shot(page, "p5a-05-routing");

  // The owner is away: the first Billing ticket goes to Sam.
  await page.getByRole("button", { name: "Account menu" }).click();
  await page.getByRole("menuitem", { name: "Set yourself away" }).click();
  await expect(page.getByLabel("You are away")).toBeVisible();
  const ticket = async (subject: string) =>
    (await (await page.request.post("/api/tickets", { data: { subject, category_id: billing.id }, headers: ORIGIN })).json()) as { id: string; assignee: string | null };
  const first = await ticket("Charged twice");
  expect(first.assignee).toBe("Sam Agent");
  await page.goto(`/tickets/${first.id}`);
  await expect(page.getByText(/Routed to Sam Agent in Billing team \(rule “Billing and email” · least busy in Billing team · 0 open\)/)).toBeVisible();
  await expect(page.getByLabel("Team").locator("option:checked")).toHaveText("Billing team");
  await shot(page, "p5a-06-routed");

  // Back: the owner now has fewer open tickets than Sam.
  await page.getByRole("button", { name: "Account menu" }).click();
  await page.getByRole("menuitem", { name: "Set yourself available" }).click();
  await expect(page.getByLabel("You are available")).toBeVisible();
  expect((await ticket("Invoice address wrong")).assignee).toBe("Ramin Example");

  // Email tickets are routed too: both have one open ticket, so the one assigned longest ago (Sam) gets it.
  const mailbox = `support-${run}@acmehelp.com`;
  const acct = await page.request.post("/api/email-accounts", {
    data: { name: "Support", address: mailbox, display_name: "Acme Support", password: "app-password-123", imap_host: "imap.acme.test", smtp_host: "smtp.acme.test" },
    headers: ORIGIN,
  });
  expect(acct.status()).toBe(201);
  const raw = [
    `From: Jane Doe <jane-${run}@example.org>`,
    `To: ${mailbox}`,
    "Subject: Where is my refund?",
    `Message-ID: <refund-${run}@example.org>`,
    `Date: ${new Date().toUTCString()}`,
    "MIME-Version: 1.0",
    "Content-Type: text/plain; charset=utf-8",
    "",
    "Hello, I'm still waiting for my refund.",
    "",
  ].join("\r\n");
  expect((await page.request.post(`/api/dev/mailbox/${encodeURIComponent(mailbox)}/deliver`, { data: raw, headers: { ...ORIGIN, "content-type": "message/rfc822" } })).ok()).toBeTruthy();
  await nav(page, "Tickets");
  const row = page.getByRole("row").filter({ hasText: "Where is my refund?" });
  await expect(row).toBeVisible({ timeout: 60_000 });
  await expect(row).toContainText("Sam Agent");
  await expect(row).toContainText("Billing team");
  await page.getByLabel("Team").selectOption({ label: "My teams" });
  await expect(page.getByRole("row").filter({ hasText: "Invoice address wrong" })).toBeVisible();
  await shot(page, "p5a-07-tickets");
  await agentCtx.close();

  // Dark theme: the new screens use only the design system's tokens.
  await page.evaluate(() => (document.documentElement.dataset.theme = "dark"));
  await nav(page, "API tools");
  await expect(page.getByText("Used by Acme Help")).toBeVisible();
  await shot(page, "p5a-08-tools-dark");
  await nav(page, "Settings");
  await page.getByRole("navigation", { name: "Settings sections" }).getByRole("link", { name: "Routing" }).click();
  await expect(page.getByLabel("Routing rules")).toBeVisible();
  await shot(page, "p5a-09-routing-dark");
});
