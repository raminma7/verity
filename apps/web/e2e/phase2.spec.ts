import http from "node:http";

import { expect, test, type Page } from "@playwright/test";

/**
 * Phase 2: knowledge base → Ask with citations → chatbot on a customer site → handoff to a person.
 * Run against an API started with VERITY_LLM_PROVIDER=fake and VERITY_EMBEDDING_PROVIDER=fake (no network)
 * and a worker (`make worker`) processing the queue.
 */
const SHOTS = process.env.E2E_SHOTS;
const PASSWORD = "Correct-horse-42";
const APP = process.env.E2E_BASE_URL ?? "http://localhost:3000";
const ORIGIN = { Origin: APP };

async function shot(page: Page, name: string) {
  if (SHOTS) await page.screenshot({ path: `${SHOTS}/${name}.png`, fullPage: true });
}

async function nav(page: Page, name: string) {
  await page.getByRole("navigation", { name: "Main" }).getByRole("link", { name, exact: true }).click();
}

async function ownerWithPlan(page: Page) {
  const email = `kb-${Date.now()}@example.com`;
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
  return email;
}

const POLICY = [
  "Refund policy.",
  "Customers on an annual plan can get a full refund within 30 days of purchase.",
  "Refunds go back to the original payment method and arrive within 5 to 10 business days.",
  "",
  "Shipping.",
  "Orders over 50 euros ship for free. Express delivery costs 9 euros.",
].join("\n");

test("knowledge base, Ask, chatbot widget and live handoff", async ({ page, browser }) => {
  test.setTimeout(120_000);
  await ownerWithPlan(page);

  // ---- Knowledge base: upload and watch it index -------------------------------------------------
  await nav(page, "Knowledge base");
  await expect(page.getByText("Your knowledge base is empty")).toBeVisible();
  await shot(page, "p2-01-knowledge-empty");
  await page.getByRole("link", { name: "Add knowledge" }).first().click();
  await page.locator('input[type="file"]').setInputFiles({ name: "refund-policy.txt", mimeType: "text/plain", buffer: Buffer.from(POLICY) });
  await expect(page.getByText(/Done · \d+ chunks/)).toBeVisible({ timeout: 20_000 });
  await shot(page, "p2-02-upload-queue");
  await page.getByRole("link", { name: "← Knowledge base" }).click();
  const row = page.getByRole("row").filter({ hasText: "refund-policy.txt" });
  await expect(row.getByText("Indexed")).toBeVisible();
  await shot(page, "p2-03-library");
  await row.getByRole("link", { name: "refund-policy.txt" }).click();
  await expect(page.getByText("Customers on an annual plan").first()).toBeVisible();
  await expect(page.getByText("Index", { exact: true })).toBeVisible(); // PipelineTrace step
  await shot(page, "p2-04-document");

  // ---- Ask: a cited answer --------------------------------------------------------------------------
  await nav(page, "Ask");
  await page.getByRole("textbox", { name: "Question" }).fill("How long do refunds take to arrive?");
  await page.keyboard.press("Enter");
  const answer = page.locator(".vr-answer").last();
  await expect(answer.getByRole("button", { name: /Source 1/ })).toBeVisible({ timeout: 20_000 });
  await answer.getByRole("button", { name: /Source 1/ }).click();
  await expect(page.locator(".vr-source.is-active")).toContainText("refund-policy.txt");
  await shot(page, "p2-05-ask");
  await page.getByRole("tab", { name: "How it was answered" }).click();
  await expect(page.getByText("Verify citations")).toBeVisible();

  // ---- Chatbot: create, authorize localhost, get the snippet ----------------------------------------
  await nav(page, "Chatbots");
  await page.getByLabel("Name").fill("Acme Help");
  await page.getByRole("button", { name: "Create" }).click();
  await expect(page.getByRole("heading", { name: "Acme Help" })).toBeVisible();
  await page.getByLabel("Welcome message").fill("Hi! Ask me about refunds and shipping.");
  await page.getByRole("button", { name: "Save changes" }).click();
  await expect(page.getByText("Saved.")).toBeVisible();
  await shot(page, "p2-06-chatbot-behaviour");
  await page.getByRole("tab", { name: "Domains & install" }).click();
  await page.getByLabel("Domains, one per line").fill("localhost");
  await page.getByRole("button", { name: "Save domains" }).click();
  await expect(page.getByText("Domains saved.")).toBeVisible();
  const snippet = (await page.locator("pre.vr-code").textContent()) ?? "";
  expect(snippet).toContain("widget.js");
  await shot(page, "p2-07-chatbot-install");

  // ---- A customer site embeds the widget ------------------------------------------------------------
  // A real server, so the browser applies the same rules it would to a customer's site.
  const html = `<!doctype html><html><head><title>Acme shop</title></head><body style="font-family:sans-serif;padding:40px"><h1>Acme shop</h1><p>Welcome.</p>${snippet}</body></html>`;
  const server = http.createServer((_, res) => res.writeHead(200, { "content-type": "text/html" }).end(html));
  await new Promise<void>((ok) => server.listen(4000, "127.0.0.1", ok));
  const site = await browser.newContext({ viewport: { width: 1280, height: 860 } });
  const visitor = await site.newPage();
  await visitor.goto("http://localhost:4000/");
  const w = visitor.frameLocator("iframe[data-verity]");
  await w.getByRole("button", { name: "Ask us" }).click();
  await expect(w.getByText("Hi! Ask me about refunds and shipping.")).toBeVisible();
  await w.getByRole("textbox").fill("How long do refunds take?");
  await w.getByRole("button", { name: "Send" }).click();
  await expect(w.getByText("Sources")).toBeVisible({ timeout: 20_000 });
  await expect(w.getByText("1. refund-policy.txt")).toBeVisible();
  await shot(visitor, "p2-08-widget-answer");

  // Nothing relevant: no AI call, the no-answer message and an offer to talk to a person.
  await w.getByRole("textbox").fill("Can I pay with zebra xylophones?");
  await w.getByRole("button", { name: "Send" }).click();
  await expect(w.getByText("I couldn't find this in our documentation.")).toBeVisible({ timeout: 20_000 });
  // No email up front: if nobody replies in time, the chat offers a form that asks for it (Phase 3).
  await w.locator(".vr-widget-chip", { hasText: "Talk to a person" }).click();
  await expect(w.getByText(/passed this to the team/)).toBeVisible();
  await shot(visitor, "p2-09-widget-handoff");

  // ---- The team replies from the dashboard -----------------------------------------------------------
  await nav(page, "Conversations");
  await expect(page.getByText("1 customer is waiting for a person")).toBeVisible({ timeout: 15_000 });
  await shot(page, "p2-10-conversations");
  await page.getByRole("link", { name: /How long do refunds take/ }).click();
  await page.getByRole("textbox", { name: "Reply" }).fill("Hi! Zebras aren’t accepted, but cards and PayPal are.");
  await page.getByRole("button", { name: "Send reply" }).click();
  await expect(page.locator(".vr-agent-msg")).toContainText("cards and PayPal");
  await shot(page, "p2-11-conversation");
  await expect(w.getByText("Hi! Zebras aren’t accepted, but cards and PayPal are.")).toBeVisible({ timeout: 15_000 });
  await shot(visitor, "p2-12-widget-agent-reply");

  // ---- A site that isn't authorized can't show the widget ---------------------------------------------
  const other = await site.newPage();
  await other.goto("http://shop.localhost:4000/");
  await other.waitForTimeout(2500);
  await expect(other.frameLocator("iframe[data-verity]").getByRole("button", { name: "Ask us" })).toHaveCount(0);
  await site.close();
  server.close();
});
