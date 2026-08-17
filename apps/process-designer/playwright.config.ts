import { defineConfig, devices } from "@playwright/test";

// Runs against the already-running docker-compose stack (infra/) - these
// are integration/E2E tests exercising real login + real gateway calls, not
// isolated component tests (that's what vitest/tests/ already covers). No
// webServer entry here on purpose: process-designer is served as a static
// export via nginx inside the stack, not started by this config.
export default defineConfig({
  testDir: "./e2e",
  fullyParallel: false,
  retries: 0,
  reporter: [["list"]],
  use: {
    baseURL: process.env.PROCESS_DESIGNER_BASE_URL || "http://localhost:3002",
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
