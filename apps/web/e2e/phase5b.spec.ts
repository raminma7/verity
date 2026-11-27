import { expect, test, type Page } from "@playwright/test";

/**
 * Phase 5B with the test payment provider (the same event path as Stripe's webhooks; Stripe is never called):
 * buy Starter on the hosted checkout → upgrade to Growth, prorated, from the plans page → buy a token package →
 * a declining card makes the renewal fail ("Past due") → a working card and "Retry payment now" recover it →
 * a downgrade is scheduled for renewal, cancel and resume. Analytics shows the workspace's tickets and AI usage in
 * 7 and 30 days and a custom range, as charts and tables, in light and dark themes. A system administrator adds a
 * token package and reads MRR, revenue and failed payments.
 * Run against an API and worker with the fake AI providers and VERITY_MAIL_TRANSPORT=fake, as in CI.
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
    const mails = (await (await page.request.get(`/api/dev/outbox?to=${encodeURIComponent(to)}`)).json()) as { links: string[]; subject: string }[];
    const found = mails.flatMap((m) => m.links).find((l) => l.includes(path));
    if (found) return new URL(found).pathname + new URL(found).search;
    await page.waitForTimeout(250);
  }
  throw new Error(`no ${path} email for ${to}`);
}

async function subjects(page: Page, to: string): Promise<string[]> {
  return ((await (await page.request.get(`/api/dev/outbox?to=${encodeURIComponent(to)}`)).json()) as { subject: string }[]).map((m) => m.subject);
}

test("Payments, token packages and analytics", async ({ page, browser }) => {
  test.setTimeout(240_000);
  const run = Date.now();
  const email = `billing-${run}@example.com`;
  const r = await page.request.post("/api/auth/register", { data: { full_name: "Ramin Example", email, password: PASSWORD }, headers: ORIGIN });
  expect(r.status()).toBe(202);
  await page.goto(await link(page, email, "/verify-email"));
  await expect(page).toHaveURL(/dashboard/);

  // ---- Buy Starter on the (test) hosted checkout -------------------------------------------------------------
  await nav(page, "Plans");
  await page.getByRole("article", { name: "Starter plan" }).getByRole("button", { name: "Choose Starter" }).click();
  await expect(page).toHaveURL(/\/checkout\/[0-9a-f-]{36}$/);
  await expect(page.getByText("Test mode")).toBeVisible();
  await page.getByRole("button", { name: /^Pay / }).click();
  await expect(page).toHaveURL(/\/billing\?paid=1/);
  await expect(page.getByText("Payment received. Your plan is active.")).toBeVisible();
  const history = page.getByRole("table");
  await expect(history.getByText("Subscription", { exact: true })).toBeVisible();
  await expect(history.getByText("Paid", { exact: true })).toBeVisible();

  // ---- Upgrade to Growth: prorated, applied now ---------------------------------------------------------------
  await nav(page, "Plans");
  await page.getByRole("article", { name: "Growth plan" }).getByRole("button", { name: "Upgrade to Growth" }).click();
  const dialog = page.getByRole("dialog", { name: "Upgrade to Growth" });
  await expect(dialog.getByText("Due today")).toBeVisible();
  await expect(dialog.getByText("Unused Starter")).toBeVisible();
  await shot(page, "p5b-01-upgrade-preview");
  await dialog.getByRole("button", { name: "Pay and upgrade" }).click();
  await expect(page).toHaveURL(/\/billing\?upgraded=Growth/);
  await expect(page.getByText("Upgraded to Growth.")).toBeVisible();
  await expect(page.getByRole("table").getByText("Upgrade", { exact: true })).toBeVisible();
  await expect(page.getByRole("table").getByText(/Upgrade to Growth \(monthly, prorated\)/)).toBeVisible();

  // ---- A token package --------------------------------------------------------------------------------------
  await page.getByRole("link", { name: "Buy more AI tokens" }).click();
  await expect(page.getByRole("heading", { name: "AI token packages" })).toBeVisible();
  await page.getByRole("article", { name: "1M tokens" }).getByRole("button", { name: "Buy 1M tokens" }).click();
  await expect(page).toHaveURL(/\/checkout\//);
  await expect(page.getByText("Token package: 1M tokens")).toBeVisible();
  await page.getByRole("button", { name: /^Pay / }).click();
  await expect(page).toHaveURL(/\/billing\/tokens\?paid=1/);
  await expect(page.getByText("The tokens are in your balance.")).toBeVisible();
  await expect(page.getByText("1M", { exact: true })).toBeVisible();
  await expect(page.getByRole("table").getByText("Available")).toBeVisible();
  await shot(page, "p5b-02-tokens");

  // ---- A declined renewal makes the subscription past due -----------------------------------------------------
  await nav(page, "Billing");
  await page.getByRole("button", { name: "Payment methods and invoices" }).click();
  await expect(page).toHaveURL(/\/billing\/test-portal/);
  await page.getByRole("tab", { name: "Use a card that declines" }).click();
  await expect(page.getByText("The test card now declines payments.")).toBeVisible();
  await page.getByRole("button", { name: "Run the renewal now" }).click();
  await expect(page.getByText("The renewal job ran for this workspace.")).toBeVisible();
  await page.getByRole("link", { name: "Back to Billing" }).click();
  await expect(page.getByText("The last renewal payment failed.")).toBeVisible();
  await expect(page.getByText(/^Past due · retrying /)).toBeVisible();
  await expect(page.getByRole("table").getByText(/^Failed · retry /)).toBeVisible();
  await expect(page.getByRole("table").getByText(/Your card was declined/)).toBeVisible();
  await shot(page, "p5b-03-past-due");
  expect((await subjects(page, email)).some((s) => s.startsWith("Payment failed"))).toBeTruthy();

  // A working card, then "Retry payment now".
  await page.getByRole("button", { name: "Update payment method" }).click();
  await page.getByRole("tab", { name: "Test card that pays" }).click();
  await expect(page.getByText("The test card now approves payments.")).toBeVisible();
  await page.getByRole("link", { name: "Back to Billing" }).click();
  await page.getByRole("button", { name: "Retry payment now" }).click();
  await expect(page.getByText("Payment received. The subscription is active again.")).toBeVisible();
  await expect(page.getByText("Active", { exact: true })).toBeVisible();
  await expect(page.getByRole("table").getByText(/^Paid on attempt 2/)).toBeVisible();

  // ---- A downgrade waits for the renewal; cancel and resume ---------------------------------------------------
  await nav(page, "Plans");
  await page.getByRole("article", { name: "Starter plan" }).getByRole("button", { name: "Switch at renewal" }).click();
  await page.getByRole("dialog").getByRole("button", { name: "Switch at renewal" }).click();
  await expect(page.getByText(/^Starter starts when the current period ends/)).toBeVisible();
  await nav(page, "Billing");
  await expect(page.getByText(/^Switches to Starter on /)).toBeVisible();
  page.once("dialog", (d) => d.accept());
  await page.getByRole("button", { name: "Cancel subscription" }).click();
  await expect(page.getByText(/^Cancelled\. Growth stays active until /)).toBeVisible();
  await expect(page.getByText(/^Ends on /).first()).toBeVisible();
  await page.getByRole("button", { name: "Resume subscription" }).click();
  await expect(page.getByText("The subscription will renew as usual.")).toBeVisible();
  await shot(page, "p5b-04-billing");

  // ---- Analytics ----------------------------------------------------------------------------------------------
  const ids: string[] = [];
  for (const [subject, priority] of [
    ["Order missing", "urgent"],
    ["Refund please", "normal"],
    ["Invoice copy", "low"],
  ]) {
    const t = await page.request.post("/api/tickets", { data: { subject, priority }, headers: ORIGIN });
    expect(t.status()).toBe(201);
    ids.push((await t.json()).id);
  }
  for (const status of ["resolved", "open", "resolved"]) {
    expect((await page.request.patch(`/api/tickets/${ids[1]}`, { data: { status }, headers: ORIGIN })).ok()).toBeTruthy();
  }
  expect((await page.request.post("/api/dev/simulate-usage", { data: { input_tokens: 2000, output_tokens: 300, count: 3 }, headers: ORIGIN })).ok()).toBeTruthy();

  await nav(page, "Analytics");
  await expect(page.getByRole("heading", { name: "Support analytics" })).toBeVisible();
  await expect(page.getByText(/30 days · daily · times in /)).toBeVisible();
  const created = page.locator(".vr-stat").filter({ hasText: "Tickets created" });
  await expect(created.locator(".vr-stat-num")).toHaveText("3");
  await expect(page.locator(".vr-stat").filter({ hasText: "Reopen rate" }).locator(".vr-stat-num")).toHaveText("50%");
  await expect(page.getByRole("table").filter({ hasText: "First response target" }).getByText("Urgent")).toBeVisible();
  await expect(page.getByRole("region", { name: "Weighted AI tokens" })).toContainText("weighted · 3 requests");
  await shot(page, "p5b-05-analytics-light");

  await page.getByRole("tab", { name: "7 days" }).click();
  await expect(page.getByText(/7 days · daily/)).toBeVisible();
  await expect(created.locator(".vr-stat-num")).toHaveText("3");
  // A custom range of four months uses weekly buckets.
  await page.getByRole("tab", { name: "Custom" }).click();
  const to = new Date();
  const from = new Date(to.getTime() - 119 * 86400000);
  const iso = (d: Date) => d.toISOString().slice(0, 10);
  await page.getByRole("textbox", { name: "From" }).fill(iso(from));
  await page.getByRole("textbox", { name: "To", exact: true }).fill(iso(to));
  await page.getByRole("button", { name: "Apply" }).click();
  await expect(page.getByText(/days · weekly/)).toBeVisible();
  // Every chart has its table.
  const volume = page.getByRole("region", { name: "Ticket volume by source" });
  await volume.getByRole("tab", { name: "Table" }).click();
  await expect(volume.getByRole("columnheader", { name: "Week" })).toBeVisible();
  await expect(volume.getByRole("columnheader", { name: "Added by the team" })).toBeVisible();

  await page.evaluate(() => localStorage.setItem("verity.theme", "dark"));
  await page.reload();
  await expect(page.getByRole("heading", { name: "Support analytics" })).toBeVisible();
  await expect(page.locator(".vr-stat").filter({ hasText: "Tickets created" }).locator(".vr-stat-num")).toHaveText("3");
  await shot(page, "p5b-06-analytics-dark");
  await page.evaluate(() => localStorage.setItem("verity.theme", "light"));

  // SLA targets live in Settings → Tickets.
  await page.goto("/settings/tickets");
  await expect(page.getByRole("heading", { name: "Response targets (SLA)" })).toBeVisible();
  await page.getByLabel("Urgent: first response within, in hours").fill("2");
  await page.getByRole("button", { name: "Save targets" }).click();
  await expect(page.getByText(/^Targets saved\./)).toBeVisible();

  // ---- The system administrator -------------------------------------------------------------------------------
  const ctx = await browser.newContext();
  const admin = await ctx.newPage();
  await admin.goto("/sign-in");
  await admin.getByLabel("Work email").fill("admin@example.com");
  await admin.getByLabel("Password").fill("Admin-pass-123");
  await admin.getByRole("button", { name: "Sign in" }).click();
  await expect(admin).toHaveURL(/dashboard|workspaces|admin/);
  await admin.goto("/admin?tab=packages");
  await admin.getByRole("button", { name: "New package" }).click();
  await admin.getByLabel("Name").fill(`Big pack ${run}`);
  await admin.getByLabel("Key").fill(`big-${run}`);
  await admin.getByLabel("Weighted tokens").fill("20000000");
  await admin.getByLabel("Price (before tax)").fill("150");
  await admin.getByRole("button", { name: "Create package" }).click();
  await expect(admin.getByText(`Big pack ${run}`)).toBeVisible();
  await admin.goto("/admin?tab=reports");
  await expect(admin.locator(".vr-stat").filter({ hasText: "MRR · USD" })).toBeVisible();
  // The workspace's failed renewal was paid on retry: it counts as recovered.
  await expect(admin.locator(".vr-stat").filter({ hasText: "Failed payments" })).toContainText(/[1-9]\d* recovered on retry/);
  await expect(admin.getByRole("region", { name: "Revenue · USD" })).toBeVisible();
  await shot(admin, "p5b-07-admin-reports");
  await admin.goto("/admin?tab=payments");
  await expect(admin.getByText("Test provider: nothing is charged")).toBeVisible();
  await expect(admin.getByText("checkout.completed").first()).toBeVisible();
  await ctx.close();

  // The workspace sees the new package (it's on sale in its currency).
  await page.goto("/billing/tokens");
  await expect(page.getByRole("article", { name: `Big pack ${run}` })).toBeVisible();
});
