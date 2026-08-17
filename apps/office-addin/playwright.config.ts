import { defineConfig, devices } from "@playwright/test";

// Runs against the already-running docker-compose stack (infra/), same
// pattern as apps/user-ui/playwright.config.ts. No webServer entry here on
// purpose: office-addin is served as a static export via nginx inside the
// stack, not started by this config.
//
// office-addin is a special case (see e2e/README.md): it is a Word
// task-pane add-in (Office.js) that can never reach real content in a
// plain browser without a mocked `window.Office` global injected via
// `page.addInitScript()` before the page's own scripts run - see
// e2e/office-mock.ts.
export default defineConfig({
  testDir: "./e2e",
  fullyParallel: false,
  retries: 0,
  reporter: [["list"]],
  use: {
    baseURL: process.env.OFFICE_ADDIN_BASE_URL || "http://localhost:3006",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"] },
    },
  ],
});
