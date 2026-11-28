import { expect, test, type Browser, type Page } from "@playwright/test";

/**
 * Phase 6. Privacy: a system administrator caps retention; the owner sets data retention within the cap,
 * exports the workspace (a one-time ZIP link by email), deletes the workspace and restores it in the grace period.
 * Spam: the trained model files a lottery email in Spam without an AI call; a business inquiry it isn't sure about
 * becomes a ticket marked "Possible spam" and "Move to Spam" closes it. A ticket gets an AI summary. Languages:
 * the interface switches to Arabic (right to left) and through Spanish, German, French and Italian.
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
  for (let i = 0; i < 80; i++) {
    const mails = (await (await page.request.get(`/api/dev/outbox?to=${encodeURIComponent(to)}`)).json()) as { links: string[] }[];
    const found = mails.flatMap((m) => m.links).find((l) => l.includes(path));
    if (found) return new URL(found).pathname + new URL(found).search;
    await page.waitForTimeout(250);
  }
  throw new Error(`no ${path} email for ${to}`);
}

/** Signs up, verifies and buys Starter through the test provider (as the API does for the hosted checkout). */
async function ownerWithPlan(page: Page, email: string, name = "Ramin Example") {
  const r = await page.request.post("/api/auth/register", { data: { full_name: name, email, password: PASSWORD }, headers: ORIGIN });
  expect(r.status()).toBe(202);
  await page.goto(await link(page, email, "/verify-email"));
  await expect(page).toHaveURL(/dashboard/);
  const plans = (await (await page.request.get("/api/plans")).json()) as { id: string; key: string }[];
  const co = await (await page.request.post("/api/billing/checkout", { data: { plan_id: plans.find((p) => p.key === "starter")!.id }, headers: ORIGIN })).json();
  expect((await page.request.post(`/api/billing/checkout/${co.checkout_id}/confirm`, { headers: ORIGIN })).ok()).toBeTruthy();
  await page.reload();
}

async function adminPage(browser: Browser): Promise<{ page: Page; close: () => Promise<void> }> {
  const ctx = await browser.newContext();
  const page = await ctx.newPage();
  await page.goto("/sign-in");
  await page.getByLabel("Work email").fill("admin@example.com");
  await page.getByLabel("Password").fill("Admin-pass-123");
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page).toHaveURL(/dashboard|workspaces|admin/);
  return { page, close: () => ctx.close() };
}

async function setClosedTicketCap(browser: Browser, days: string) {
  const admin = await adminPage(browser);
  await admin.page.goto("/admin?tab=settings");
  const field = admin.page.getByLabel("Closed tickets", { exact: true });
  await field.fill(days);
  await admin.page.getByRole("button", { name: "Save defaults" }).click();
  await expect(admin.page.getByText("Defaults saved.")).toBeVisible();
  await admin.close();
}

/** A raw RFC 5322 message, as a customer's mail program would send it. */
function rawMail(o: { from: string; to: string; subject: string; body: string; id: string }) {
  const h: Record<string, string> = {
    From: o.from,
    To: o.to,
    Subject: o.subject,
    "Message-ID": `<${o.id}@example.org>`,
    Date: new Date().toUTCString(),
    "MIME-Version": "1.0",
    "Content-Type": "text/plain; charset=utf-8",
  };
  return `${Object.entries(h)
    .map(([k, v]) => `${k}: ${v}`)
    .join("\r\n")}\r\n\r\n${o.body.replaceAll("\n", "\r\n")}\r\n`;
}

async function deliver(page: Page, mailbox: string, raw: string) {
  const r = await page.request.post(`/api/dev/mailbox/${encodeURIComponent(mailbox)}/deliver`, { data: raw, headers: { ...ORIGIN, "content-type": "message/rfc822" } });
  expect(r.ok(), await r.text()).toBeTruthy();
}

test("Data retention, export, and deleting and restoring a workspace", async ({ page, browser }) => {
  test.setTimeout(180_000);
  const run = Date.now();
  const email = `privacy-${run}@example.com`;
  await ownerWithPlan(page, email);
  const wsName = (await (await page.request.get("/api/me")).json()).workspaces[0].name as string;

  // ---- The platform caps closed tickets at a year; the workspace keeps them 90 days ---------------------------
  await setClosedTicketCap(browser, "365");
  try {
    await page.goto("/settings/workspace");
    const retention = page.locator(".vr-card").filter({ has: page.getByRole("heading", { name: "Data retention" }) });
    const row = retention.getByRole("row").filter({ hasText: "Closed tickets, with their timeline and email" });
    await expect(row.getByRole("cell", { name: "1 year", exact: true })).toBeVisible();
    const select = row.getByRole("combobox");
    await expect(select.locator("option", { hasText: "2 years" })).toHaveCount(0); // longer than the platform allows
    await select.selectOption({ label: "90 days" });
    await retention.getByRole("button", { name: "Save retention" }).click();
    await expect(page.getByText("Retention saved. Older data is erased within the hour.")).toBeVisible();
    await shot(page, "p6-01-retention");
    const saved = (await (await page.request.get("/api/workspace/retention")).json()) as { retention: Record<string, number | null> };
    expect(saved.retention.closed_tickets).toBe(90);
  } finally {
    await setClosedTicketCap(browser, "");
  }

  // ---- Export the workspace: a one-time link by email --------------------------------------------------------
  await page.goto("/settings/workspace");
  const exportCard = page.locator(".vr-card").filter({ has: page.getByRole("heading", { name: "Export workspace data" }) });
  await exportCard.getByRole("button", { name: "Export workspace data" }).click();
  await expect(page.getByText("Export requested. We’ll email you when it’s ready.")).toBeVisible();
  await page.goto(await link(page, email, "/exports/"));
  await expect(page.getByRole("heading", { name: "Your export is ready" })).toBeVisible();
  await shot(page, "p6-02-export-ready");
  const [download] = await Promise.all([page.waitForEvent("download"), page.getByRole("link", { name: "Download the export" }).click()]);
  expect(download.suggestedFilename()).toMatch(/\.zip$/);
  const path = await download.path();
  expect(path).toBeTruthy();
  // The link works once.
  const token = new URL(page.url()).pathname.split("/").pop()!;
  expect((await page.request.get(`/api/exports/${token}/download`)).status()).toBe(410);

  // ---- Delete the workspace, then restore it within the 30 days -----------------------------------------------
  await page.goto("/settings/workspace");
  const danger = page.locator(".vr-danger-zone").filter({ has: page.getByRole("heading", { name: "Delete this workspace" }) });
  await danger.getByRole("button", { name: "Delete workspace…" }).click();
  await danger.getByLabel(`Type the workspace name: ${wsName}`).fill(wsName);
  await danger.getByLabel("Your password").fill(PASSWORD);
  await danger.getByRole("button", { name: `Delete ${wsName}` }).click();
  await expect(page).toHaveURL(/dashboard/);
  const banner = page.getByText(`${wsName} is scheduled for deletion.`);
  await expect(banner).toBeVisible();
  await expect(page.getByText(/It is closed for everyone and will be erased on/)).toBeVisible();
  await shot(page, "p6-03-scheduled-for-deletion");
  const notice = await link(page, email, "/workspaces");
  expect(notice).toContain("/workspaces");
  // While it is closed, the workspace's data can't be reached.
  expect((await page.request.get("/api/tickets")).status()).toBe(403);
  await page.getByRole("button", { name: "Restore workspace" }).click();
  await expect(banner).toBeHidden();
  // Open again, with its data; tickets wait for a plan, since deleting ended the subscription.
  const me = (await (await page.request.get("/api/me")).json()) as { workspaces: { name: string; deletion_requested_at: string | null }[] };
  expect(me.workspaces.find((w) => w.name === wsName)?.deletion_requested_at).toBeNull();
  expect((await page.request.get("/api/workspace/retention")).ok()).toBeTruthy();
  expect((await page.request.get("/api/tickets")).status()).toBe(402);
});

test("Spam decided by the trained model, possible spam, a ticket summary and six languages", async ({ page }) => {
  test.setTimeout(240_000);
  const run = Date.now();
  const email = `languages-${run}@example.com`;
  const mailbox = `support-${run}@acme.example`;
  await ownerWithPlan(page, email);
  for (const [name, description] of [
    ["General", ""],
    ["Shipping", "Orders that haven't arrived, delivery and tracking"],
  ]) {
    expect((await page.request.post("/api/ticket-categories", { data: { name, description }, headers: ORIGIN })).status()).toBe(201);
  }

  // ---- Connect a mailbox ------------------------------------------------------------------------------------------
  await nav(page, "Email");
  const connectForm = page.getByRole("form", { name: "Connect a mailbox" });
  await connectForm.getByLabel("Email address", { exact: true }).fill(mailbox);
  await connectForm.getByRole("textbox", { name: "Sender name" }).fill("Acme Support");
  await connectForm.getByRole("textbox", { name: "App password" }).fill("app-password-123");
  await connectForm.getByRole("button", { name: "Sign in and connect" }).click();
  await expect(page.getByText(`${mailbox} is connected.`)).toBeVisible();

  // ---- A lottery email: Spam, decided by the trained model, no AI call ----------------------------------------
  const calls = async () =>
    ((await (await page.request.get("/api/usage/breakdown")).json()) as { by_operation: { requests: number }[] }).by_operation.reduce((n, r) => n + r.requests, 0);
  const before = await calls();
  await deliver(
    page,
    mailbox,
    rawMail({
      from: "Prize Desk <winner@lotto-intl.example>",
      to: mailbox,
      subject: "CONGRATULATIONS!!! YOU HAVE WON $1,000,000",
      body: "Dear Winner, your email was selected in our international lottery. To claim your prize of $1,000,000 send your full name, bank account and a processing fee of $250 today! Click here to receive your money. Act now, offer expires!!!",
      id: `lottery-${run}`,
    }),
  );
  await page.getByRole("navigation", { name: "Email sections" }).getByRole("link", { name: "Spam" }).click();
  const held = page.getByRole("list", { name: "Held email" });
  await expect(held.getByText("CONGRATULATIONS!!! YOU HAVE WON $1,000,000")).toBeVisible({ timeout: 30_000 });
  await held.getByText("CONGRATULATIONS!!! YOU HAVE WON $1,000,000").click();
  await expect(page.getByText(/^The spam filter rated it \d+% likely to be spam/)).toBeVisible();
  await shot(page, "p6-04-spam-by-model");
  const spam = (await (await page.request.get("/api/email/spam")).json()) as { items: { subject: string; verdict: { decided_by: string } }[] };
  expect(spam.items.find((m) => m.subject.startsWith("CONGRATULATIONS"))?.verdict.decided_by).toBe("model");
  expect(await calls()).toBe(before); // spam never costs an AI call

  // ---- An inquiry the model isn't sure about: a ticket marked "Possible spam" -----------------------------------
  await deliver(
    page,
    mailbox,
    rawMail({
      from: "Anna <anna@studio.example>",
      to: mailbox,
      subject: "Collaboration idea",
      body: "Hi, I run a small design studio and love your products. Would you be open to a collaboration or a discount for our clients? Happy to talk.",
      id: `collab-${run}`,
    }),
  );
  await nav(page, "Tickets");
  const collab = page.getByRole("row").filter({ hasText: "Collaboration idea" });
  await expect(collab).toBeVisible({ timeout: 30_000 });
  await collab.getByRole("link").click();
  await expect(page.getByText("Possible spam")).toBeVisible();
  await expect(page.getByText(/The spam filter wasn’t sure about this email/)).toBeVisible();
  await shot(page, "p6-05-possible-spam");
  await page.getByRole("button", { name: "Move to Spam" }).click();
  await expect(page.getByLabel("Status")).toHaveValue("closed");
  await nav(page, "Email");
  await page.getByRole("navigation", { name: "Email sections" }).getByRole("link", { name: "Spam" }).click();
  await expect(page.getByRole("list", { name: "Held email" }).getByText("Collaboration idea")).toBeVisible();

  // ---- A real request, and its AI summary -----------------------------------------------------------------------
  await deliver(
    page,
    mailbox,
    rawMail({
      from: "Jane Doe <jane.doe@example.org>",
      to: mailbox,
      subject: "My order hasn't arrived",
      body: "Hello, I ordered a blue jacket two weeks ago (order 48213) and it still hasn't arrived. The tracking page hasn't changed since last Monday. Could you check what happened? Thanks, Jane",
      id: `order-${run}`,
    }),
  );
  await nav(page, "Tickets");
  const order = page.getByRole("row").filter({ hasText: "My order hasn't arrived" });
  await expect(order).toBeVisible({ timeout: 30_000 });
  await order.getByRole("link").click();
  await expect(page.getByText("Possible spam")).toHaveCount(0);
  const summary = page.getByLabel("Summary");
  await summary.getByRole("button", { name: "Summarize" }).click();
  await expect(summary.getByText(/AI · updated/)).toBeVisible();
  await expect(summary.locator("p")).not.toHaveText(/written by AI for the team/);
  await expect(summary.getByRole("button", { name: "Update summary" })).toBeVisible();
  await shot(page, "p6-06-ticket-summary");
  const ticketUrl = page.url();

  // ---- Arabic: the whole interface right to left ---------------------------------------------------------------
  await page.goto("/settings");
  await page.getByLabel("Language").selectOption({ label: "العربية" });
  await page.getByRole("button", { name: "Save profile" }).click();
  await expect(page.locator("html")).toHaveAttribute("dir", "rtl");
  await expect(page.locator("html")).toHaveAttribute("lang", "ar");
  await expect(page.getByText("تم حفظ الملف الشخصي.")).toBeVisible();
  await expect(page.locator("nav").first().locator('a[href="/tickets"]')).toContainText("التذاكر");
  await shot(page, "p6-07-settings-ar");
  await page.goto(ticketUrl);
  await expect(page.getByLabel("Summary").or(page.getByLabel("الملخص"))).toBeVisible();
  await shot(page, "p6-08-ticket-ar");
  await page.goto("/dashboard");
  await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
  await shot(page, "p6-09-dashboard-ar");
  await page.emulateMedia({ colorScheme: "dark" });
  await page.goto("/analytics");
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("تحليلات الدعم");
  await expect(page.locator(".vr-spin:not(.vr-spin-sm)")).toHaveCount(0, { timeout: 15_000 });
  await shot(page, "p6-10-analytics-ar-dark");
  await page.emulateMedia({ colorScheme: "light" });

  // ---- Spanish, German, French, Italian, then back to English (left to right again) ----------------------------
  const settings: Record<string, string> = { es: "Configuración", de: "Einstellungen", fr: "Paramètres", it: "Impostazioni", en: "Settings" };
  for (const locale of ["es", "de", "fr", "it", "en"]) {
    const set = await page.request.patch("/api/me", { data: { locale }, headers: ORIGIN });
    expect(set.ok(), `${set.status()} ${await set.text()}`).toBeTruthy();
    await page.goto("/settings");
    await expect(page.locator("html")).toHaveAttribute("lang", locale);
    await expect(page.locator("html")).toHaveAttribute("dir", "ltr");
    await expect(page.locator("nav").first().locator('a[href="/settings"]')).toContainText(settings[locale]);
    if (locale !== "en") {
      await page.goto(ticketUrl);
      await expect(page.getByRole("heading", { level: 1 })).toHaveText("My order hasn't arrived");
      await shot(page, `p6-11-ticket-${locale}`);
    }
  }
  // The emails a person gets follow their language too.
  expect((await page.request.patch("/api/me", { data: { locale: "fr" }, headers: ORIGIN })).ok()).toBeTruthy();
  expect((await page.request.post("/api/me/exports", { headers: ORIGIN })).status()).toBe(202);
  for (let i = 0; i < 80; i++) {
    const mails = (await (await page.request.get(`/api/dev/outbox?to=${encodeURIComponent(email)}`)).json()) as { subject: string }[];
    if (mails.some((m) => m.subject === "Votre export de données Verity est prêt")) break;
    if (i === 79) throw new Error("no French export email");
    await page.waitForTimeout(250);
  }
  expect((await page.request.patch("/api/me", { data: { locale: "en" }, headers: ORIGIN })).ok()).toBeTruthy();
});
