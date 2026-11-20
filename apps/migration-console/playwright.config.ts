import { defineConfig, devices } from "@playwright/test";

// Runs against the already-running docker-compose stack (infra/) - these
// are integration/E2E tests exercising real login + real gateway calls, not
// isolated component tests (that's what vitest/tests/ already covers). No
// webServer entry here on purpose: migration-console is served as a static
// export via nginx inside the stack, not started by this config.
export default defineConfig({
  testDir: "./e2e",
  fullyParallel: false,
  // One worker (P83-S8): the paired-installations fixture grants a role to
  // the shared test account for the length of one test. A parallel worker's
  // login can recompute that account's effective permissions concurrently
  // and leave a pre-grant entry in permission-service's materialized cache,
  // so the grant appears not to apply. The suite is four short tests.
  workers: 1,
  retries: 0,
  reporter: [["list"]],
  use: {
    baseURL: process.env.MIGRATION_CONSOLE_BASE_URL || "http://localhost:3004",
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
