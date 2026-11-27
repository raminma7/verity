import http from "node:http";

import { expect, test, type Browser, type FrameLocator, type Page } from "@playwright/test";

/**
 * Phase 3: ticket categories → form builder → the chatbot offers a form, prefills it and the customer
 * confirms → the ticket with its form data and conversation → reopen on reply → the handoff fallback when
 * nobody replies → an agent turns a chat into a ticket.
 * Run against an API with VERITY_LLM_PROVIDER=fake and VERITY_EMBEDDING_PROVIDER=fake and a worker.
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
  const email = `tickets-${Date.now()}@example.com`;
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

/** A visitor on the customer's site, with the chat open. */
async function visitor(browser: Browser): Promise<{ page: Page; w: FrameLocator; close: () => Promise<void> }> {
  const ctx = await browser.newContext({ viewport: { width: 1280, height: 900 } });
  const page = await ctx.newPage();
  await page.goto("http://localhost:4100/");
  const w = page.frameLocator("iframe[data-verity]");
  await w.getByRole("button", { name: "Ask us" }).click();
  await expect(w.getByText("Hi! How can we help?")).toBeVisible();
  return { page, w, close: () => ctx.close() };
}

async function say(w: FrameLocator, text: string) {
  await w.getByRole("textbox", { name: "Type a message…" }).fill(text);
  await w.getByRole("button", { name: "Send" }).last().click();
}

const RETURN_FIELDS = [
  { key: "order_number", label: "Order number", type: "text", required: true, validation: { pattern: "[0-9]{5,8}" } },
  { key: "email", label: "Email", type: "email", required: true },
  { key: "reason", label: "Reason", type: "select", required: true, options: ["Damaged", "Wrong size", "Changed my mind"] },
  { key: "items", label: "Items", type: "multiselect", options: ["Shoes", "Shirt", "Hat"] },
  { key: "quantity", label: "Quantity", type: "number", validation: { min: 1, max: 10, integer: true } },
  { key: "purchased_on", label: "Purchase date", type: "date" },
  { key: "details", label: "Details", type: "textarea" },
  { key: "consent", label: "I agree to the return policy", type: "checkbox", required: true },
];

test("forms, tickets and the handoff fallback", async ({ page, browser }) => {
  test.setTimeout(240_000);
  await ownerWithPlan(page);

  // ---- Settings → Tickets: categories ------------------------------------------------------------------
  await nav(page, "Settings");
  await page.getByRole("navigation", { name: "Settings sections" }).getByRole("link", { name: "Tickets" }).click();
  for (const name of ["Returns", "Sales"]) {
    await page.getByLabel("New category").fill(name);
    await page.getByRole("button", { name: "Add category" }).click();
    await expect(page.getByText(`${name} added.`)).toBeVisible();
  }
  await page.getByRole("button", { name: "Move Returns down" }).click();
  await expect(page.locator(".vr-cat-row").first()).toContainText("Sales");
  await shot(page, "p3-01-categories");

  // ---- A chatbot on a customer site, and a return form (set up through the API) ----------------------------
  const bot = await (await page.request.post("/api/chatbots", { data: { name: "Acme Help" }, headers: ORIGIN })).json();
  await page.request.patch(`/api/chatbots/${bot.id}`, { data: { allowed_domains: ["localhost"], welcome_message: "Hi! How can we help?" }, headers: ORIGIN });
  const cats = (await (await page.request.get("/api/ticket-categories")).json()) as { id: string; name: string }[];
  const returns = cats.find((c) => c.name === "Returns")!;
  const created = await page.request.post("/api/forms", {
    data: {
      name: "Return request",
      description: "We’ll arrange the pickup.",
      category_id: returns.id,
      activation_condition: "The customer wants to return or exchange an order.",
      status: "active",
      fields: RETURN_FIELDS,
      chatbot_ids: [bot.id],
    },
    headers: ORIGIN,
  });
  expect(created.status()).toBe(201);

  // ---- Form builder: a lead form, built in the UI ----------------------------------------------------------
  await nav(page, "Forms");
  await expect(page.getByRole("link", { name: "Return request" })).toBeVisible();
  await expect(page.getByText("Contact form", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "New form" }).click();
  await page.getByLabel("Name", { exact: true }).fill("Contact me later");
  await page.getByLabel("Ticket category").selectOption({ label: "Sales" });
  await page.getByLabel("Activation condition").fill("The visitor seems interested in our product and would like us to get in touch later.");
  await page.getByLabel("Acme Help").check();
  await page.getByLabel("Add a field").selectOption("email");
  await page.getByRole("switch", { name: "Required" }).click();
  await page.getByLabel("Add a field").selectOption("select");
  await page.getByLabel("Label", { exact: true }).fill("Best time to call");
  await page.getByLabel("Options").fill("Morning\nAfternoon");
  await page.getByRole("tab", { name: "Active" }).click();
  // The live preview renders what customers will see.
  await expect(page.locator(".vr-wpreview").getByLabel("Best time to call")).toBeVisible();
  await shot(page, "p3-02-form-builder");
  await page.getByRole("button", { name: "Create form" }).click();
  await expect(page.getByText("version 1")).toBeVisible();
  await nav(page, "Forms");
  await expect(page.getByRole("link", { name: "Contact me later" })).toBeVisible();
  await shot(page, "p3-03-forms");

  const snippet = (await (await page.request.get(`/api/chatbots/${bot.id}`)).json()).snippet as string;
  const html = `<!doctype html><html><head><title>Acme shop</title></head><body style="font-family:sans-serif;padding:40px"><h1>Acme shop</h1>${snippet}</body></html>`;
  const server = http.createServer((_, res) => res.writeHead(200, { "content-type": "text/html" }).end(html));
  await new Promise<void>((ok) => server.listen(4100, "127.0.0.1", ok));

  try {
    // ---- The assistant offers the matching form, prefills it, and the customer confirms -----------------------
    const v1 = await visitor(browser);
    await say(v1.w, "Hi, I want to return order 12345, the shoes arrived damaged. My email is jane@example.com");
    const card = v1.w.locator(".vr-wform-card").filter({ hasText: "Return request" });
    await expect(card).toBeVisible({ timeout: 20_000 });
    await shot(v1.page, "p3-04-widget-offer");
    await card.getByRole("button", { name: "Fill in the form" }).click();
    await expect(card.getByLabel("Order number")).toHaveValue("12345");
    await expect(card.getByLabel("Email")).toHaveValue("jane@example.com");
    await expect(card.getByText(/We filled in what you told us/)).toBeVisible();
    // Nothing is ticked for the customer, and nothing is sent until they confirm.
    await expect(card.getByLabel("I agree to the return policy")).not.toBeChecked();
    await card.getByRole("button", { name: "Confirm and send" }).click();
    await expect(card.getByText("This field is required.").first()).toBeVisible();
    await card.getByLabel("Reason").selectOption("Damaged");
    await card.getByLabel("Shoes").check();
    await card.getByLabel("I agree to the return policy").check();
    await shot(v1.page, "p3-05-widget-review");
    await card.getByRole("button", { name: "Confirm and send" }).click();
    await expect(v1.w.getByText(/registered as ticket #1/)).toBeVisible();
    await expect(v1.w.getByText("Sent · ticket #1")).toBeVisible();
    await shot(v1.page, "p3-06-widget-ticket");

    // ---- The ticket: form data, conversation, assignment, internal note, resolve ---------------------------
    await nav(page, "Tickets");
    const row = page.getByRole("row").filter({ hasText: "Return request: 12345" });
    await expect(row.getByText("New")).toBeVisible();
    await shot(page, "p3-07-tickets");
    await row.getByRole("link").click();
    const formCard = page.getByLabel("Submitted form data");
    await expect(formCard.getByText("12345")).toBeVisible();
    await expect(formCard.getByText("Damaged")).toBeVisible();
    await expect(page.getByLabel("Conversation history").getByText(/return order 12345/)).toBeVisible();
    await page.getByLabel("Assignee").selectOption({ label: "Ramin Example" });
    await expect(page.getByText(/assigned it to Ramin Example/)).toBeVisible();
    await page.getByRole("tab", { name: "Internal note" }).click();
    await page.getByRole("textbox", { name: "Internal note" }).fill("Pickup booked with the courier.");
    await page.getByRole("button", { name: "Add note" }).click();
    await expect(page.locator(".vr-tl-item.is-note")).toContainText("Pickup booked");
    await page.getByLabel("Status").selectOption("resolved");
    await expect(page.locator("header").getByText("Resolved")).toBeVisible();
    await shot(page, "p3-08-ticket");
    // The note never reaches the customer.
    await expect(v1.w.getByText("Pickup booked")).toHaveCount(0);

    // ---- The customer replies: the resolved ticket reopens --------------------------------------------------
    await say(v1.w, "One more question about my return, when is the pickup?");
    await expect(page.getByText("Reopened: the customer replied.")).toBeVisible({ timeout: 20_000 });
    await expect(page.locator("header").getByText("Open", { exact: true })).toBeVisible();
    await v1.close();

    // ---- The lead form, offered from its own condition -------------------------------------------------------
    const v2 = await visitor(browser);
    await say(v2.w, "I'm really interested in your product, could someone contact me in the afternoon?");
    const lead = v2.w.locator(".vr-wform-card").filter({ hasText: "Contact me later" });
    await expect(lead).toBeVisible({ timeout: 20_000 });
    await lead.getByRole("button", { name: "Fill in the form" }).click();
    await expect(lead.getByLabel("Best time to call")).toHaveValue("Afternoon");
    await lead.getByLabel("Email").fill("lead@example.com");
    await lead.getByRole("button", { name: "Confirm and send" }).click();
    await expect(v2.w.getByText(/registered as ticket #2/)).toBeVisible();
    await v2.close();

    // ---- Handoff fallback: nobody replies, the chat offers the contact form ---------------------------------
    await page.goto(`/chatbots/${bot.id}`);
    await page.getByRole("tab", { name: "Handoff" }).click();
    await page.getByLabel("Wait before offering it (seconds)").fill("10");
    await page.getByRole("button", { name: "Save changes" }).click();
    await expect(page.getByText("Saved.")).toBeVisible();
    await shot(page, "p3-09-handoff-settings");
    const v3 = await visitor(browser);
    await say(v3.w, "zebra xylophone quantum");
    await v3.w.locator(".vr-widget-chip", { hasText: "Talk to a person" }).click();
    await expect(v3.w.getByText(/passed this to the team/)).toBeVisible();
    const contact = v3.w.locator(".vr-wform-card").filter({ hasText: "Contact the team" });
    await expect(contact).toBeVisible({ timeout: 45_000 });
    await expect(contact.getByText(/No one from the team is free right now/)).toBeVisible();
    await shot(v3.page, "p3-10-widget-handoff-form");
    await contact.getByRole("button", { name: "Fill in the form" }).click();
    await contact.getByLabel("Your email").fill("waiting@example.com");
    await contact.getByLabel("How can we help?").fill("Please call me back about zebras.");
    await contact.getByRole("button", { name: "Confirm and send" }).click();
    await expect(v3.w.getByText(/registered as ticket #3/)).toBeVisible();
    // The assistant has the chat again.
    await expect(v3.w.getByText("Waiting for the team…")).toHaveCount(0);
    await shot(v3.page, "p3-11-widget-handoff-ticket");
    await v3.close();

    // ---- An agent turns an escalated conversation into a ticket ------------------------------------------------
    const v4 = await visitor(browser);
    await say(v4.w, "My invoice shows the wrong address");
    await expect(v4.w.locator(".vr-widget-chip", { hasText: "Talk to a person" })).toBeVisible({ timeout: 20_000 });
    await v4.w.locator(".vr-widget-chip", { hasText: "Talk to a person" }).click();
    await nav(page, "Conversations");
    await page.getByRole("link", { name: /My invoice shows the wrong address/ }).click();
    await page.getByRole("button", { name: "Create ticket" }).click();
    await page.getByLabel("Category").selectOption({ label: "Sales" });
    await page.getByRole("button", { name: "Create ticket" }).last().click();
    await expect(page.getByRole("link", { name: /Ticket #4/ })).toBeVisible();
    await shot(page, "p3-12-conversation-ticket");
    await page.getByRole("link", { name: /Ticket #4/ }).click();
    await page.getByRole("textbox", { name: "Reply to the customer" }).fill("We’ve fixed the address on your invoice.");
    await page.getByRole("button", { name: "Send reply" }).click();
    await expect(page.locator(".vr-tl-item.is-agent")).toContainText("fixed the address");
    await expect(v4.w.getByText("We’ve fixed the address on your invoice.")).toBeVisible({ timeout: 20_000 });
    await v4.close();

    // ---- A visitor leaves their email while waiting and closes the page: the ticket opens on its own -----------
    const v5 = await visitor(browser);
    await say(v5.w, "quantum zebra parking question");
    await v5.w.locator(".vr-widget-chip", { hasText: "Talk to a person" }).click();
    await expect(v5.w.getByText("We’ve passed this to the team. You’ll get a reply here.")).toBeVisible();
    await expect(v5.w.getByText(/Might you leave before someone replies/)).toBeVisible();
    await v5.w.getByRole("textbox", { name: "Your email" }).fill("gone@example.com");
    await v5.w.getByRole("button", { name: "Send", exact: true }).first().click();
    await expect(v5.w.getByText(/get back to you at gone@example.com/)).toBeVisible();
    await shot(v5.page, "p3-13-widget-email-while-waiting");
    await v5.close(); // the visitor leaves
    await nav(page, "Tickets");
    await expect(page.getByRole("row").filter({ hasText: "gone@example.com" })).toBeVisible({ timeout: 45_000 });
    await page.getByRole("row").filter({ hasText: "gone@example.com" }).getByRole("link").click();
    await expect(page.getByText(/Opened automatically: the visitor asked for a person/)).toBeVisible();
    await shot(page, "p3-14-ticket-after-visitor-left");

    await nav(page, "Tickets");
    await page.getByRole("tab", { name: /^All/ }).click();
    await expect(page.getByRole("row")).toHaveCount(6);
    await page.getByRole("textbox", { name: "Search tickets" }).fill("zebras");
    await expect(page.getByRole("row")).toHaveCount(2);
  } finally {
    server.close();
  }
});
