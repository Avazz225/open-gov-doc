import { test, expect, loginViaUi } from "./fixtures";

// Configurable email templates (/email-templates/, post-roadmap phase 30,
// ADR 0111) - not just a page-load smoke test: creates a real catch-all
// template against the running notification-service, asserts it shows up
// in the table, then edits and deletes it again (cleanup, same convention
// as user-management.spec.ts's create/delete round-trip).
test("creates, edits, and deletes an email template", async ({ page }) => {
  // `/email-templates/` is gated behind `admin.notification_config`
  // (RequireCapability) - `users-admin` only carries `admin.user_management`
  // and does not have this capability (confirmed live against the running
  // stack, Phase 79 Session 3), so this spec must log in as the new
  // `notification-admin` technical account (bootstrap.py) instead. This, not
  // stale UI text, was the actual reason this spec previously timed out.
  await loginViaUi(page, { username: "notification-admin", password: "notification-admin" });
  await page.goto("/email-templates/");

  const form = page.getByRole("form", { name: "Vorlage konfigurieren" });
  await expect(form).toBeVisible({ timeout: 15_000 });

  await form.getByLabel("Anlass").selectOption("license.invalid");
  await form.getByLabel("Betreff").fill("[E2E] Lizenz ungültig: {reason}");
  await form.getByLabel("Text").fill("E2E-Testtext: {reason}");
  await form.getByRole("button", { name: "Speichern" }).click();

  const row = page.locator("tr", { hasText: "[E2E] Lizenz ungültig: {reason}" });
  await expect(row).toBeVisible({ timeout: 15_000 });
  await expect(row.locator("td").nth(1)).toHaveText("Alle");

  await row.getByRole("button", { name: "Bearbeiten" }).click();
  await expect(form.getByLabel("Anlass")).toBeDisabled();
  await form.getByLabel("Text").fill("E2E-Testtext (bearbeitet): {reason}");
  await form.getByRole("button", { name: "Speichern" }).click();
  await expect(row).toBeVisible({ timeout: 15_000 });

  await row.getByRole("button", { name: "Löschen" }).click();
  await expect(row).toHaveCount(0, { timeout: 15_000 });
});
