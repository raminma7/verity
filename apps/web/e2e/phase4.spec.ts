import http from "node:http";

import { expect, test, type Browser, type FrameLocator, type Page } from "@playwright/test";

/**
 * Phase 4: connect a mailbox → a customer email becomes a ticket in the right category with an automatic
 * first reply → the agent replies by email in the thread → the customer's reply reopens the ticket → spam
 * and promotions wait in Spam; "Not spam" opens a ticket → a chat visitor who left an email gets the
 * agent's reply by email, and their email reply comes back into the conversation.
 * Run against an API and worker with VERITY_MAIL_TRANSPORT=fake and the fake AI providers.
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

async function ownerWithPlan(page: Page) {
  const email = `email-${Date.now()}@example.com`;
  const r = await page.request.post("/api/auth/register", { data: { full_name: "Ramin Example", email, password: PASSWORD }, headers: ORIGIN });
  expect(r.status()).toBe(202);
  let verify = "";
  for (let i = 0; i < 20 && !verify; i++) {
    const mails = (await (await page.request.get(`/api/dev/outbox?to=${encodeURIComponent(email)}`)).json()) as { links: string[] }[];
    verify = mails.flatMap((m) => m.links).find((l) => l.includes("/verify-email")) ?? "";
    if (!verify) await page.waitForTimeout(250);
  }
  await page.goto(new URL(verify).pathname + new URL(verify).search);
  await expect(page).toHaveURL(/dashboard/);
  const plans = (await (await page.request.get("/api/plans")).json()) as { id: string; key: string }[];
  const co = await (await page.request.post("/api/billing/checkout", { data: { plan_id: plans.find((p) => p.key === "starter")!.id }, headers: ORIGIN })).json();
  expect((await page.request.post(`/api/billing/checkout/${co.checkout_id}/confirm`, { headers: ORIGIN })).ok()).toBeTruthy();
  await page.reload();
}

type Sent = { to: string; subject: string; message_id: string; in_reply_to: string; references: string; auto_submitted: string; text: string };

/** A raw RFC 5322 message, as a customer's mail program would send it. */
function rawMail(o: { from: string; to: string; subject: string; body: string; id: string; inReplyTo?: string; headers?: Record<string, string> }) {
  const h: Record<string, string> = {
    From: o.from,
    To: o.to,
    Subject: o.subject,
    "Message-ID": `<${o.id}@example.org>`,
    Date: new Date().toUTCString(),
    "MIME-Version": "1.0",
    "Content-Type": "text/plain; charset=utf-8",
    ...(o.inReplyTo ? { "In-Reply-To": o.inReplyTo, References: o.inReplyTo } : {}),
    ...(o.headers ?? {}),
  };
  return `${Object.entries(h)
    .map(([k, v]) => `${k}: ${v}`)
    .join("\r\n")}\r\n\r\n${o.body.replaceAll("\n", "\r\n")}\r\n`;
}

async function deliver(page: Page, mailbox: string, raw: string) {
  const r = await page.request.post(`/api/dev/mailbox/${encodeURIComponent(mailbox)}/deliver`, { data: raw, headers: { ...ORIGIN, "content-type": "message/rfc822" } });
  expect(r.ok(), await r.text()).toBeTruthy();
}

async function sentFrom(page: Page, mailbox: string): Promise<Sent[]> {
  return (await (await page.request.get(`/api/dev/mailbox/${encodeURIComponent(mailbox)}/sent`)).json()) as Sent[];
}

async function waitForSent(page: Page, mailbox: string, match: (m: Sent) => boolean): Promise<Sent> {
  for (let i = 0; i < 120; i++) {
    const hit = (await sentFrom(page, mailbox)).find(match);
    if (hit) return hit;
    await page.waitForTimeout(250);
  }
  throw new Error(`no matching email sent from ${mailbox}`);
}

async function visitor(browser: Browser): Promise<{ page: Page; w: FrameLocator; close: () => Promise<void> }> {
  const ctx = await browser.newContext({ viewport: { width: 1280, height: 900 } });
  const page = await ctx.newPage();
  await page.goto("http://localhost:4101/");
  const w = page.frameLocator("iframe[data-verity]");
  await w.getByRole("button", { name: "Ask us" }).click();
  await expect(w.getByText("Hi! How can we help?")).toBeVisible();
  return { page, w, close: () => ctx.close() };
}

test("email: mailboxes, tickets, threads, spam and chat replies by email", async ({ page, browser }) => {
  test.setTimeout(240_000);
  const run = Date.now();
  const mailbox = `support-${run}@acmehelp.com`;
  const customer = `jane-${run}@example.org`;
  await ownerWithPlan(page);
  for (const [name, description] of [
    ["General", ""],
    ["Shipping", "Orders that haven't arrived, delivery and tracking"],
    ["Billing", "Invoices, refunds and payments"],
  ]) {
    const c = await page.request.post("/api/ticket-categories", { data: { name, description }, headers: ORIGIN });
    expect(c.status()).toBe(201);
  }

  // ---- Connect a mailbox ------------------------------------------------------------------------------------
  await nav(page, "Email");
  await expect(page.getByText("No mailbox connected yet")).toBeVisible();
  const connectForm = page.getByRole("form", { name: "Connect a mailbox" });
  await connectForm.getByLabel("Email address", { exact: true }).fill(mailbox);
  await connectForm.getByRole("textbox", { name: "Sender name" }).fill("Acme Support");
  await connectForm.getByRole("textbox", { name: "App password" }).fill("wrong-password");
  await connectForm.getByRole("button", { name: "Sign in and connect" }).click();
  await expect(page.getByText(/didn’t accept this username and app password|didn't accept this username and app password/)).toBeVisible();
  await connectForm.getByRole("textbox", { name: "App password" }).fill("app-password-123");
  await connectForm.getByRole("button", { name: "Sign in and connect" }).click();
  await expect(page.getByText(`${mailbox} is connected.`)).toBeVisible();
  await expect(page.getByText("1 of 3")).toBeVisible();
  await expect(page.getByLabel("Connected mailboxes").getByText("Connected", { exact: true })).toBeVisible();
  await shot(page, "p4-01-mailboxes");

  // ---- A customer writes: a ticket in the right category, with an automatic first reply --------------------
  await deliver(page, mailbox, rawMail({ from: `Jane Doe <${customer}>`, to: mailbox, subject: "My order hasn't arrived", body: "Hello, my order 55123 hasn't arrived yet. Can you check the delivery?", id: `first-${run}` }));
  await nav(page, "Tickets");
  const row = page.getByRole("row").filter({ hasText: "My order hasn't arrived" });
  await expect(row).toBeVisible({ timeout: 30_000 });
  await expect(row.getByText("Email")).toBeVisible();
  const auto = await waitForSent(page, mailbox, (m) => m.auto_submitted === "auto-replied");
  expect(auto.to).toBe(customer);
  expect(auto.subject).toContain("[#1]");
  expect(auto.text).toContain("#1");
  expect(auto.in_reply_to).toBe(`<first-${run}@example.org>`);
  await row.getByRole("link").click();
  await expect(page.getByLabel("Category")).toHaveValue(/.+/);
  await expect(page.getByLabel("Category").locator("option:checked")).toHaveText("Shipping");
  await expect(page.getByRole("article", { name: "Email received" })).toContainText("order 55123");
  await expect(page.getByRole("article", { name: "Email sent" }).first()).toContainText("Automatic first reply");

  // ---- The agent replies by email, in the thread ---------------------------------------------------------------
  await expect(page.getByRole("tab", { name: "Reply by email" })).toHaveAttribute("aria-selected", "true");
  await expect(page.getByText(`To ${customer}, from ${mailbox}.`)).toBeVisible();
  await page.getByRole("textbox", { name: "Reply to the customer" }).fill("We found your parcel: it arrives tomorrow.");
  await page.getByRole("button", { name: "Send reply" }).click();
  const agentCard = page.getByRole("article", { name: "Email sent" }).filter({ hasText: "We found your parcel" });
  await expect(agentCard).toBeVisible();
  await expect(agentCard.getByText("Sent", { exact: true })).toBeVisible({ timeout: 20_000 });
  const agentMail = await waitForSent(page, mailbox, (m) => m.text.includes("We found your parcel"));
  expect(agentMail.subject).toBe("Re: My order hasn't arrived [#1]");
  expect(agentMail.in_reply_to).toBe(auto.message_id);
  expect(agentMail.auto_submitted).toBe("");
  await shot(page, "p4-02-email-ticket");

  // ---- Resolved, then the customer replies: the ticket reopens -------------------------------------------------
  await page.getByLabel("Status").selectOption("resolved");
  await expect(page.locator("header").getByText("Resolved")).toBeVisible();
  await deliver(
    page,
    mailbox,
    rawMail({ from: `Jane Doe <${customer}>`, to: mailbox, subject: agentMail.subject, body: "It came, thanks! One more question about the invoice.\n\nOn Monday Acme wrote:\n> We found your parcel", id: `reply-${run}`, inReplyTo: agentMail.message_id }),
  );
  await expect(page.getByText("Reopened: the customer replied.")).toBeVisible({ timeout: 30_000 });
  await expect(page.locator("header").getByText("Open", { exact: true })).toBeVisible();
  await expect(page.getByRole("article", { name: "Email received" }).filter({ hasText: "It came, thanks!" })).toBeVisible();
  await shot(page, "p4-03-reopened-by-email");

  // ---- Spam and promotions wait for review ------------------------------------------------------------------------
  await deliver(page, mailbox, rawMail({ from: `Shop News <news-${run}@shop.example>`, to: mailbox, subject: "50% off this week only", body: "Our biggest sale. Unsubscribe here.", id: `promo-${run}`, headers: { "List-Unsubscribe": "<mailto:u@shop.example>", "List-Id": "news.shop.example" } }));
  await deliver(page, mailbox, rawMail({ from: `Prize Desk <lucky-${run}@prize.example>`, to: mailbox, subject: "You have won", body: "Claim your prize now.", id: `spam-${run}`, headers: { "X-Spam-Flag": "YES" } }));
  await nav(page, "Email");
  await page.getByRole("navigation", { name: "Email sections" }).getByRole("link", { name: "Spam" }).click();
  const held = page.getByRole("list", { name: "Held email" });
  await expect(held.getByText("50% off this week only")).toBeVisible({ timeout: 30_000 });
  await expect(held.getByText("You have won")).toBeVisible({ timeout: 30_000 });
  await held.getByText("50% off this week only").click();
  await expect(page.getByText(/Held because it has an unsubscribe link/)).toBeVisible();
  await shot(page, "p4-04-spam-review");
  await page.getByRole("button", { name: "Not spam: make it a ticket" }).click();
  await expect(page.getByText(/Moved to tickets as/)).toBeVisible();
  await held.getByText("You have won").click();
  await page.getByRole("button", { name: "Delete", exact: true }).click();
  await expect(page.getByText("Nothing to review")).toBeVisible();
  await nav(page, "Tickets");
  await expect(page.getByRole("row").filter({ hasText: "50% off this week only" })).toBeVisible();
  await expect(page.getByRole("row").filter({ hasText: "You have won" })).toHaveCount(0);

  // ---- Settings → Email --------------------------------------------------------------------------------------------
  await nav(page, "Settings");
  await page.getByRole("navigation", { name: "Settings sections" }).getByRole("link", { name: "Email" }).click();
  await expect(page.getByLabel("Preview")).toContainText("ticket #42");
  await page.getByRole("tab", { name: "Written by AI" }).click();
  await expect(page.getByText(/An acknowledgement, in the customer’s language/)).toBeVisible();
  await page.getByRole("button", { name: "Save automatic reply" }).click();
  await expect(page.getByText("Automatic reply saved.")).toBeVisible();
  await shot(page, "p4-05-email-settings");

  // ---- Chat: the visitor leaves an email; the agent's reply reaches it; their email reply comes back --------------
  const bot = await (await page.request.post("/api/chatbots", { data: { name: "Acme Help" }, headers: ORIGIN })).json();
  await page.request.patch(`/api/chatbots/${bot.id}`, {
    data: { allowed_domains: ["localhost"], welcome_message: "Hi! How can we help?", escalation: { enabled: true, ask_email: true, ticket_fallback: true, wait_seconds: 10 } },
    headers: ORIGIN,
  });
  const snippet = (await (await page.request.get(`/api/chatbots/${bot.id}`)).json()).snippet as string;
  const html = `<!doctype html><html><head><title>Acme shop</title></head><body style="font-family:sans-serif;padding:40px"><h1>Acme shop</h1>${snippet}</body></html>`;
  const server = http.createServer((_, res) => res.writeHead(200, { "content-type": "text/html" }).end(html));
  await new Promise<void>((ok) => server.listen(4101, "127.0.0.1", ok));
  const visitorEmail = `visitor-${run}@example.org`;
  try {
    const v = await visitor(browser);
    await v.w.getByRole("textbox", { name: "Type a message…" }).fill("zebra xylophone parking question");
    await v.w.getByRole("button", { name: "Send" }).last().click();
    await v.w.locator(".vr-widget-chip", { hasText: "Talk to a person" }).click();
    await expect(v.w.getByText("We’ve passed this to the team. You’ll get a reply here, and by email if you leave your address.")).toBeVisible();
    await v.w.getByRole("textbox", { name: "Your email" }).fill(visitorEmail);
    await v.w.getByRole("button", { name: "Send", exact: true }).first().click();
    await expect(v.w.getByText(new RegExp(`get back to you at ${visitorEmail}`))).toBeVisible();
    // Nobody replies in time: the ticket opens and its confirmation goes to the visitor.
    const confirm = await waitForSent(page, mailbox, (m) => m.to === visitorEmail && m.subject.startsWith("Your request is ticket"));
    expect(confirm.auto_submitted).toBe("auto-generated");
    await expect(v.w.getByText(new RegExp(`The team will get back to you at ${visitorEmail}`)).last()).toBeVisible({ timeout: 30_000 });
    await shot(v.page, "p4-06-widget-ticket-auto");
    await v.close(); // the visitor leaves

    await nav(page, "Conversations");
    await page.getByRole("link", { name: /zebra xylophone parking question/ }).click();
    await page.getByRole("textbox", { name: "Reply" }).fill("Sorry for the wait: parking is free after 6 pm.");
    await page.getByRole("button", { name: "Send reply" }).click();
    await expect(page.getByText(`also emailed to ${visitorEmail}`)).toBeVisible();
    const chatMail = await waitForSent(page, mailbox, (m) => m.to === visitorEmail && m.text.includes("parking is free after 6 pm"));
    expect(chatMail.in_reply_to).toBe(confirm.message_id);
    await deliver(page, mailbox, rawMail({ from: visitorEmail, to: mailbox, subject: chatMail.subject, body: "Great, and on Sundays?", id: `chat-${run}`, inReplyTo: chatMail.message_id }));
    await expect(page.locator(".vr-bubble").filter({ hasText: "Great, and on Sundays?" })).toBeVisible({ timeout: 30_000 });
    await expect(page.locator(".vr-bubble").filter({ hasText: "Great, and on Sundays?" }).getByText("By email")).toBeVisible();
    await shot(page, "p4-07-conversation-email-reply");
  } finally {
    server.close();
  }
});
