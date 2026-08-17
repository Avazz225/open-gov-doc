import { test, expect, loginViaUi } from "./fixtures";

// User management CRUD (/users/) - the most important admin-ui flow per
// the task: create a throwaway user through the real UI/gateway, verify it
// shows up in the list, then delete it again in the same test so the
// shared dev stack is left clean (no fixture teardown needed here, unlike
// user-ui's isolated-folder fixture, since the delete IS the flow under
// test, not just cleanup).
test("creates a user, sees it in the list, and deletes it again", async ({ page }) => {
  await loginViaUi(page);
  await page.goto("/users/");

  const username = `e2e-admin-ui-${Date.now()}`;
  const email = `${username}@example.invalid`;

  const createUserForm = page.getByRole("form", { name: "Nutzer anlegen" });
  await createUserForm.getByLabel("Benutzername").fill(username);
  await createUserForm.getByLabel("E-Mail").fill(email);
  await createUserForm.getByLabel("Passwort").fill("e2e-Passw0rd!");
  await createUserForm.getByLabel("Vorname").fill("E2E");
  await createUserForm.getByLabel("Nachname").fill("Test");
  await createUserForm.getByRole("button", { name: "Anlegen" }).click();

  const userRow = page.getByRole("row", { name: new RegExp(username) });
  await expect(userRow).toBeVisible({ timeout: 15_000 });
  await expect(userRow).toContainText(email);

  await userRow.getByRole("button", { name: "Löschen" }).click();
  await expect(page.getByRole("row", { name: new RegExp(username) })).toHaveCount(0, {
    timeout: 15_000,
  });
});
