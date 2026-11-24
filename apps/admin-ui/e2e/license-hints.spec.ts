import { expect, GATEWAY_BASE_URL, loginViaUi, test, TEST_PASSWORD, TEST_USERNAME } from "./fixtures";

// P91-S6 against the running stack: the licence page lists the components
// the licence doesn't cover with their benefit, and the installation-wide
// switch hides/shows the muted sidebar group. The switch is restored at the
// end. The dev stack configures no licensable components by default
// (`REGISTRY_SERVICE_LICENSABLE_COMPONENTS={}`), so this needs a registry-
// service started with some, e.g.
// `{"workflow-service":"demo","cmis-connector":"demo"}`; without any the test
// only checks the "nothing to extend" note. Without a licence installed
// every listed component is "demo".
// `users-admin` gets `domain-admin-license` for the duration of the test
// (granted before logging in, since capabilities load at login).
const SHOTS = process.env.E2E_SCREENSHOT_DIR;
const SIZES = [
  [1400, 900],
  [768, 1000],
  [390, 844],
] as const;

test("licence page: extension hints and their switch", async ({ page, request }) => {
  const login = await request.post(`${GATEWAY_BASE_URL}/api/auth-service/login`, {
    data: { username: TEST_USERNAME, password: TEST_PASSWORD },
  });
  const auth = { Authorization: `Bearer ${(await login.json()).access_token}` };
  const roles = await (await request.get(`${GATEWAY_BASE_URL}/api/permission-service/roles`, { headers: auth })).json();
  const roleId = roles.find((r: { name: string }) => r.name === "domain-admin-license").id;
  const grant = await request.post(`${GATEWAY_BASE_URL}/api/permission-service/role-assignments`, {
    headers: auth,
    data: { principal_type: "user", principal_id: "2", role_id: roleId, resource_id: "root" },
  });
  const assignmentId = (await grant.json()).role_assignment.id;
  const components = await (
    await request.get(`${GATEWAY_BASE_URL}/api/registry-service/license-components`, { headers: auth })
  ).json();
  try {
    if (components.length === 0) {
      await loginViaUi(page);
      await page.goto("/license/");
      await expect(page.locator("#erweiterung").getByText(/keine lizenzpflichtigen/)).toBeVisible({ timeout: 30_000 });
      return;
    }
    await checkHints(page);
  } finally {
    await request.delete(`${GATEWAY_BASE_URL}/api/permission-service/role-assignments/${assignmentId}`, {
      headers: auth,
    });
  }
});

async function checkHints(page: import("@playwright/test").Page) {
  await loginViaUi(page);
  await page.goto("/license/");
  const section = page.locator("#erweiterung");
  await expect(section.getByText("Prozesse & Umlaufmappen")).toBeVisible({ timeout: 30_000 });
  await expect(section.getByText(/als geführte Abläufe/)).toBeVisible();

  const sidebarGroup = page.getByTestId("license-upgrade-hints");
  const toggle = section.getByLabel("Hinweise auf Lizenzerweiterungen in der Navigation anzeigen");
  // Controlled checkbox: it flips once the server confirmed the change.
  if (!(await toggle.isChecked())) {
    await toggle.click();
    await expect(toggle).toBeChecked();
  }
  await expect(sidebarGroup).toBeVisible();
  await expect(sidebarGroup.getByRole("link", { name: "Prozesse & Umlaufmappen" })).toBeVisible();

  for (const [width, height] of SIZES) {
    await page.setViewportSize({ width, height });
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
    expect(overflow).toBeLessThanOrEqual(0);
    if (SHOTS) await page.screenshot({ path: `${SHOTS}/license-hints-${width}.png`, fullPage: true });
  }
  await page.setViewportSize({ width: 1400, height: 900 });

  try {
    await toggle.click();
    await expect(toggle).not.toBeChecked();
    await expect(sidebarGroup).toHaveCount(0);
    // Still off after a reload - it's stored server-side.
    await page.reload();
    await expect(page.locator("#erweiterung").getByLabel(/Hinweise auf Lizenzerweiterungen/)).not.toBeChecked({
      timeout: 30_000,
    });
    await expect(page.getByTestId("license-upgrade-hints")).toHaveCount(0);
  } finally {
    const restore = page.locator("#erweiterung").getByLabel(/Hinweise auf Lizenzerweiterungen/);
    if (!(await restore.isChecked())) await restore.click();
    await expect(restore).toBeChecked();
  }
  await expect(page.getByTestId("license-upgrade-hints")).toBeVisible();
}
