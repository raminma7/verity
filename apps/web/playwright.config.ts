import { defineConfig } from "@playwright/test";

/** Expects the API (with VERITY_EMAIL_BACKEND=memory) on :8000 and the web app on :3000. See README. */
export default defineConfig({
  testDir: "e2e",
  timeout: 60_000,
  fullyParallel: false,
  use: { baseURL: process.env.E2E_BASE_URL ?? "http://localhost:3000", viewport: { width: 1440, height: 1000 } },
});
