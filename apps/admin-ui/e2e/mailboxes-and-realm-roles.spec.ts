import { test, expect, loginViaUi, GATEWAY_BASE_URL, TEST_USERNAME, TEST_PASSWORD } from "./fixtures";
import type { APIRequestContext } from "@playwright/test";

// P88-S4b: the mailbox admin page and realm-role assignment, against the
// running stack. `users-admin` gets `domain-admin-mail` for the duration of
// the mailbox test (granted before logging in, since capabilities load at
// login) and loses it again afterwards; the realm-role test works on a
// throwaway Keycloak user it creates and deletes itself.

async function adminToken(request: APIRequestContext): Promise<string> {
  const response = await request.post(`${GATEWAY_BASE_URL}/api/auth-service/login`, {
    data: { username: TEST_USERNAME, password: TEST_PASSWORD },
  });
  return (await response.json()).access_token;
}

test("creates, tests, disables and deletes a mailbox", async ({ page, request }) => {
  const token = await adminToken(request);
  const auth = { Authorization: `Bearer ${token}` };
  const roles = await (await request.get(`${GATEWAY_BASE_URL}/api/permission-service/roles`, { headers: auth })).json();
  const roleId = roles.find((r: { name: string }) => r.name === "domain-admin-mail").id;
  const grant = await request.post(`${GATEWAY_BASE_URL}/api/permission-service/role-assignments`, {
    headers: auth,
    data: { principal_type: "user", principal_id: "2", role_id: roleId, resource_id: "root" },
  });
  const assignmentId = (await grant.json()).role_assignment.id;
  page.on("dialog", (dialog) => dialog.accept());
  try {
    await loginViaUi(page);
    await page.goto("/mailboxes/");
    await expect(page.locator("tr", { hasText: "central" }).first()).toBeVisible({ timeout: 15_000 });

    const id = `e2e-${Date.now()}`;
    await page.getByRole("button", { name: "Postfach anlegen" }).click();
    const form = page.getByRole("form", { name: "Neues Postfach" });
    await form.getByLabel("Kennung").fill(id);
    await form.getByLabel("Name", { exact: true }).fill("E2E-Postfach");
    await form.getByRole("combobox", { name: /^Protokoll/ }).selectOption("pop3");
    await form.getByLabel("Server").fill("mailpit");
    await form.getByLabel("Port").fill("1110");
    await form.getByLabel("TLS").uncheck();
    await form.getByLabel("Benutzername").fill("mailconnector");
    await form.getByLabel("Passwort").fill("mailconnector_dev_only");
    // Disabled: the same mailpit account as "central" must not be polled twice.
    await form.getByLabel("Aktiv (wird abgerufen)").uncheck();

    await form.getByRole("button", { name: "Verbindung testen" }).click();
    await expect(form.getByRole("status")).toHaveText("Anmeldung erfolgreich", { timeout: 15_000 });
    await form.getByRole("button", { name: "Speichern" }).click();

    const row = page.locator("tr", { hasText: id });
    await expect(row.getByText("deaktiviert")).toBeVisible({ timeout: 15_000 });

    // Editing keeps the stored password: a wrong host fails, the original succeeds.
    await row.getByRole("button", { name: "Bearbeiten" }).click();
    const edit = page.getByRole("form", { name: "Postfach bearbeiten" });
    await expect(edit.getByLabel("Passwort")).toHaveAttribute("placeholder", "unverändert");
    await edit.getByRole("button", { name: "Verbindung testen" }).click();
    await expect(edit.getByRole("status")).toHaveText("Anmeldung erfolgreich", { timeout: 15_000 });
    await edit.getByRole("button", { name: "Abbrechen" }).click();

    await row.getByRole("button", { name: "Löschen" }).click();
    await expect(page.locator("tr", { hasText: id })).toHaveCount(0, { timeout: 15_000 });
  } finally {
    await request.delete(`${GATEWAY_BASE_URL}/api/permission-service/role-assignments/${assignmentId}`, {
      headers: auth,
    });
  }
});

test("assigns and removes a realm role for a user", async ({ page, request }) => {
  const token = await adminToken(request);
  const auth = { Authorization: `Bearer ${token}` };
  const username = `e2e-realm-${Date.now()}`;
  const created = await request.post(`${GATEWAY_BASE_URL}/api/auth-service/users`, {
    headers: auth,
    data: { username, email: `${username}@example.test`, password: "Geheim-123!", first_name: "E2E", last_name: "Realm" },
  });
  expect(created.ok()).toBeTruthy();
  const userId = (await created.json()).id;
  try {
    await loginViaUi(page);
    await page.goto("/users/");
    const section = page.getByRole("region", { name: "Realm-Rollen (Anwendungszugang)" });
    const filter = section.getByLabel("Rollen filtern");
    if (await filter.count()) await filter.fill("dms-poststelle");
    await section.getByRole("button", { name: "dms-poststelle", exact: true }).click();
    await section.getByLabel("Benutzername").fill(username);
    await section.getByRole("button", { name: "Zuweisen" }).click();
    const chip = section.locator("li.badge", { hasText: username });
    await expect(chip).toBeVisible({ timeout: 15_000 });

    await chip.getByRole("button", { name: `${username} die Rolle dms-poststelle entziehen` }).click();
    await expect(section.locator("li.badge", { hasText: username })).toHaveCount(0, { timeout: 15_000 });
  } finally {
    await request.delete(`${GATEWAY_BASE_URL}/api/auth-service/users/${userId}`, { headers: auth });
  }
});
