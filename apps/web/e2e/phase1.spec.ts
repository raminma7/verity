import { expect, test, type Page } from "@playwright/test";

const SHOTS = process.env.E2E_SHOTS; // directory for screenshots, optional
const PASSWORD = "Correct-horse-42";

async function shot(page: Page, name: string) {
  if (SHOTS) await page.screenshot({ path: `${SHOTS}/${name}.png`, fullPage: true });
}

async function nav(page: Page, name: string) {
  await page.getByRole("navigation", { name: "Main" }).getByRole("link", { name, exact: true }).click();
}

async function link(page: Page, email: string, part: string) {
  for (let i = 0; i < 20; i++) {
    const r = await page.request.get(`/api/dev/outbox?to=${encodeURIComponent(email)}`);
    const mails = (await r.json()) as { links: string[] }[];
    const found = mails.flatMap((m) => m.links).reverse().find((l) => l.includes(part));
    if (found) return new URL(found).pathname + new URL(found).search;
    await page.waitForTimeout(250);
  }
  throw new Error(`no ${part} email for ${email}`);
}

test("sign up, verify, buy a plan, see usage and invite a teammate", async ({ page }) => {
  const email = `owner-${Date.now()}@example.com`;

  await page.goto("/dashboard");
  await expect(page).toHaveURL(/sign-in/);
  await shot(page, "01-sign-in");

  await page.getByRole("link", { name: "Create an account" }).click();
  await page.getByLabel("Full name").fill("Ramin Example");
  await page.getByLabel("Work email").fill(email);
  await page.getByLabel("Password").fill(PASSWORD);
  await shot(page, "02-sign-up");
  await page.getByRole("button", { name: "Continue" }).click();
  await expect(page.getByRole("heading", { name: "Check your inbox" })).toBeVisible();
  await shot(page, "03-check-inbox");

  // Unverified accounts can sign in but can't reach the dashboard.
  await page.goto("/sign-in");
  await page.getByLabel("Work email").fill(email);
  await page.getByLabel("Password").fill(PASSWORD);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page).toHaveURL(/verify-email\?email=/);

  await page.goto(await link(page, email, "/verify-email"));
  await expect(page).toHaveURL(/dashboard/);
  await expect(page.getByText("Email verified. Your workspace is ready.")).toBeVisible();
  await expect(page.getByText("Choose a plan to turn on Verity’s AI features.")).toBeVisible();
  await shot(page, "04-dashboard-no-plan");

  // Invitations are locked without a plan.
  await nav(page, "Members");
  await expect(page.getByText("Inviting people needs an active plan.")).toBeVisible();

  await nav(page, "Plans");
  await expect(page.getByRole("article", { name: "Starter plan" })).toBeVisible();
  await shot(page, "05-plans");
  await page.getByRole("button", { name: "Choose Starter" }).click();
  await expect(page).toHaveURL(/checkout\//);
  await shot(page, "06-checkout");
  await page.getByRole("button", { name: /Pay \$29/ }).click();
  await expect(page).toHaveURL(/billing\?paid=1/);
  await expect(page.getByText("Payment received. Your plan is active.")).toBeVisible();
  await expect(page.getByRole("cell", { name: "Paid" })).toBeVisible();
  await shot(page, "07-billing");

  // Exercise the real reserve → settle path, then check the usage page.
  const ws = await page.request.get("/api/workspace");
  expect((await ws.json()).subscription.active).toBe(true);
  const sim = await page.request.post("/api/dev/simulate-usage", { data: { input_tokens: 40000, output_tokens: 8000, count: 6 }, headers: { Origin: "http://localhost:3000" } });
  expect(sim.ok()).toBeTruthy();
  await nav(page, "AI usage");
  await expect(page.getByText("Weighted tokens this period")).toBeVisible();
  await expect(page.getByRole("cell", { name: "claude-haiku-5-5" }).first()).toBeVisible();
  await shot(page, "08-usage");

  await nav(page, "Members");
  const teammate = `agent-${Date.now()}@example.com`;
  await page.getByLabel("Email").fill(teammate);
  await page.getByRole("button", { name: "Send invite" }).click();
  await expect(page.getByText(`Invitation sent to ${teammate}.`)).toBeVisible();
  await expect(page.getByRole("cell", { name: teammate })).toBeVisible();
  await shot(page, "09-members");

  await nav(page, "Dashboard");
  await expect(page.getByText("Starter plan · active")).toBeVisible();
  await shot(page, "10-dashboard");

  // Teammate accepts in a fresh browser context.
  const invite = await link(page, teammate, "/invite/");
  const other = await page.context().browser()!.newContext({ viewport: { width: 1440, height: 1000 } });
  const p2 = await other.newPage();
  await p2.goto(`http://localhost:3000${invite}`);
  await expect(p2.getByRole("heading", { name: /Join / })).toBeVisible();
  await p2.getByRole("link", { name: "Create an account" }).click();
  await p2.getByLabel("Full name").fill("Sam Agent");
  await p2.getByLabel("Work email").fill(teammate);
  await p2.getByLabel("Password").fill(PASSWORD);
  await p2.getByRole("button", { name: "Continue" }).click();
  await expect(p2.getByRole("heading", { name: "Check your inbox" })).toBeVisible();
  await p2.goto(`http://localhost:3000${await link(p2, teammate, "/verify-email")}`);
  await expect(p2.getByRole("heading", { name: /Join / })).toBeVisible();
  await p2.getByRole("button", { name: "Accept and open workspace" }).click();
  await expect(p2).toHaveURL(/dashboard/);
  // Support agents don't see billing.
  await expect(p2.getByRole("navigation", { name: "Main" }).getByRole("link", { name: "Billing" })).toHaveCount(0);
  await other.close();

  await nav(page, "Settings");
  await page.getByRole("link", { name: "Security" }).click();
  await expect(page.getByRole("button", { name: "Set up two-factor" })).toBeVisible();
  await shot(page, "11-security");

  // Dark theme
  await page.evaluate(() => (document.documentElement.dataset.theme = "dark"));
  await nav(page, "Dashboard");
  await expect(page.getByText("Starter plan · active")).toBeVisible();
  await shot(page, "12-dashboard-dark");
  await nav(page, "AI usage");
  await expect(page.getByText("Weighted tokens this period")).toBeVisible();
  await shot(page, "13-usage-dark");
});

test("system admin can see the platform", async ({ page }) => {
  await page.goto("/sign-in");
  await page.getByLabel("Work email").fill("admin@example.com");
  await page.getByLabel("Password").fill("Admin-pass-123");
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page).toHaveURL(/dashboard/);
  await nav(page, "System admin");
  await expect(page.getByText("MRR")).toBeVisible();
  await shot(page, "14-admin");
  await page.getByRole("button", { name: "Plans" }).click();
  await expect(page.getByRole("cell", { name: /Growth/ })).toBeVisible();
  await page.getByRole("button", { name: "Workspaces" }).click();
  await expect(page.getByRole("button", { name: "Adjust tokens" }).first()).toBeVisible();
  await shot(page, "15-admin-workspaces");
});

test("mobile layout", async ({ browser }) => {
  const ctx = await browser.newContext({ viewport: { width: 390, height: 844 } });
  const page = await ctx.newPage();
  await page.goto("http://localhost:3000/sign-in");
  await expect(page.getByRole("heading", { name: "Welcome back" })).toBeVisible();
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth);
  expect(overflow).toBe(false);
  await shot(page, "16-mobile-sign-in");
  await page.getByLabel("Work email").fill("admin@example.com");
  await page.getByLabel("Password").fill("Admin-pass-123");
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page).toHaveURL(/dashboard/);
  await expect(page.getByRole("button", { name: "Open navigation" })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth)).toBe(false);
  await shot(page, "17-mobile-dashboard");
  await page.getByRole("button", { name: "Open navigation" }).click();
  await expect(page.getByRole("navigation", { name: "Main" }).getByRole("link", { name: "Plans", exact: true })).toBeInViewport();
  await ctx.close();
});
