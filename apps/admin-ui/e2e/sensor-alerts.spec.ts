import { test, expect, loginViaUi, GATEWAY_BASE_URL, TEST_USERNAME, TEST_PASSWORD } from "./fixtures";

// Alerting UI (P88-S6, ADR 0245) against the running stack: a rule on the
// local storage target's fill level is created through the UI after
// checking its live value, a maintenance window is added, and both are
// removed again. `users-admin` gets `domain-admin-monitoring` for the test
// (before login - capabilities load at login) and loses it in `finally`.
test("creates an alert rule with a live preview and a maintenance window", async ({ page, request }) => {
  const login = await request.post(`${GATEWAY_BASE_URL}/api/auth-service/login`, {
    data: { username: TEST_USERNAME, password: TEST_PASSWORD },
  });
  const auth = { Authorization: `Bearer ${(await login.json()).access_token}` };
  const roles = await (await request.get(`${GATEWAY_BASE_URL}/api/permission-service/roles`, { headers: auth })).json();
  const roleId = roles.find((r: { name: string }) => r.name === "domain-admin-monitoring").id;
  const grant = await request.post(`${GATEWAY_BASE_URL}/api/permission-service/role-assignments`, {
    headers: auth,
    data: { principal_type: "user", principal_id: "2", role_id: roleId, resource_id: "root" },
  });
  const assignmentId = (await grant.json()).role_assignment.id;
  page.on("dialog", (dialog) => dialog.accept());
  const ruleName = `E2E-Alarm-${Date.now()}`;
  try {
    await loginViaUi(page);
    await page.goto("/sensors/");
    const table = page.getByRole("region", { name: "Gruppen und Sensoren" });
    await table.getByRole("button", { name: "Alarmregeln für storage.target.used_percent" }).click({ timeout: 15_000 });
    await table.getByRole("button", { name: "Alarmregel hinzufügen" }).click();

    const form = page.getByRole("form", { name: "Neue Alarmregel für storage.target.used_percent" });
    await form.getByLabel("Name").fill(ruleName);
    await form.getByLabel("Warnung über").fill("1");
    await form.getByRole("button", { name: "Aktuellen Wert prüfen" }).click();
    // The live value of both targets is far above 1 %: the preview warns,
    // and a target label can be taken over as filter.
    await expect(form.getByRole("status")).toContainText("Aktueller Wert", { timeout: 15_000 });
    await expect(form.getByRole("status").locator(".badge.warn")).toHaveText("Warnung");
    await form.getByRole("button", { name: "target=local" }).click();
    await expect(form.getByLabel("Wert 1")).toHaveValue("local");
    await form.getByRole("button", { name: "Speichern" }).click();

    const rules = table.getByLabel("Regeln von storage.target.used_percent");
    await expect(rules.getByText(ruleName)).toBeVisible({ timeout: 15_000 });

    // A maintenance window for that rule only.
    const windows = page.getByRole("region", { name: "Wartungsfenster" });
    await windows.getByRole("button", { name: "Wartungsfenster anlegen" }).click();
    const windowForm = windows.getByRole("form", { name: "Neues Wartungsfenster" });
    await windowForm.getByLabel("Name").fill(`${ruleName}-Fenster`);
    await windowForm.getByLabel("So").check();
    await windowForm.getByLabel("Täglich ab").fill("22:00");
    await windowForm.getByLabel("bis", { exact: true }).fill("02:00");
    await windowForm.getByLabel("Alle Regeln").uncheck();
    await windowForm.getByLabel(ruleName).check();
    await windowForm.getByRole("button", { name: "Speichern" }).click();
    const windowItem = windows.locator("li", { hasText: `${ruleName}-Fenster` });
    await expect(windowItem.getByText("So · 22:00–02:00")).toBeVisible({ timeout: 15_000 });
    await expect(windowItem.getByText("1 Regel(n)")).toBeVisible();

    await windowItem.getByRole("button", { name: "Löschen" }).click();
    await expect(windows.locator("li", { hasText: `${ruleName}-Fenster` })).toHaveCount(0, { timeout: 15_000 });
    await rules.locator("div", { hasText: ruleName }).last().getByRole("button", { name: "Löschen" }).click();
    await expect(table.getByText(ruleName)).toHaveCount(0, { timeout: 15_000 });
  } finally {
    // Remove leftovers of a failed run, then the grant.
    const listed = await (await request.get(`${GATEWAY_BASE_URL}/api/monitoring-service/alert-rules`, { headers: auth })).json();
    for (const rule of listed as { id: string; name: string }[]) {
      if (rule.name === ruleName) {
        await request.delete(`${GATEWAY_BASE_URL}/api/monitoring-service/alert-rules/${rule.id}`, { headers: auth });
      }
    }
    await request.delete(`${GATEWAY_BASE_URL}/api/permission-service/role-assignments/${assignmentId}`, {
      headers: auth,
    });
  }
});
