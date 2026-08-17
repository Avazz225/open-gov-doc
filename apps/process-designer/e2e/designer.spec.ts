import { apiLogin, deleteProcessDefinitionViaApi, expect, loginViaUi, test } from "./fixtures";

// `workflow-service`'s process-definitions list already accumulates a large
// number of pre-existing entries from other test suites in this dev stack
// (verified: ~380 rows) - a unique name lets this test find its own entry
// in the overview list without depending on (or being confused by) the rest.
const PROCESS_NAME = `e2e-process-designer-${Date.now()}`;

test.describe("BPMN designer", () => {
  let createdId: number | null = null;

  test.afterEach(async () => {
    // `DELETE /process-definitions/{id}` is a real hard delete (see
    // repository.py`delete_process_definition` - no soft-delete/tombstone),
    // safe here since the test never starts a process instance against the
    // definition it creates (delete would otherwise be refused with 409).
    if (createdId !== null) {
      const { access_token: token } = await apiLogin();
      await deleteProcessDefinitionViaApi(token, createdId);
      createdId = null;
    }
  });

  test("creates a new BPMN diagram, adds a task on the canvas, and saves it", async ({
    page,
  }) => {
    await loginViaUi(page);

    // "Create new" navigates to the designer without a `?id=` - bpmn-js
    // then loads the app's built-in starter diagram (a single start event).
    await page.getByRole("button", { name: "Neu erstellen" }).click();
    await page.waitForURL(/\/designer\//);

    // bpmn-js mounts asynchronously (dynamic import, ssr:false) and then
    // imports the starter XML - wait for its own canvas/palette DOM rather
    // than a fixed delay.
    const paletteTaskEntry = page.locator('.djs-palette .entry[data-action="create.task"]');
    await expect(paletteTaskEntry).toBeVisible();
    await expect(page.locator('.djs-shape[data-element-id="StartEvent_1"]')).toBeVisible();

    // Add a Task element: bpmn-js's palette uses a "click tool, then click
    // canvas to place" interaction (not HTML5 drag-and-drop), which is far
    // more reliable to drive through Playwright than a real drag gesture.
    await paletteTaskEntry.click();
    const diagramSvg = page.locator(".designer-canvas svg[data-element-id]");
    const box = await diagramSvg.boundingBox();
    if (!box) throw new Error("BPMN canvas SVG has no bounding box");
    await page.mouse.click(box.x + box.width / 2, box.y + box.height / 2 + 100);

    // A second shape (the new Task, an auto-generated `Activity_*` id)
    // joins the existing start event.
    await expect(page.locator(".djs-shape")).toHaveCount(2);

    // Save: requires the `admin.object_config` capability (see
    // docs/services/process-designer.md "Authorization") - granted to
    // `users-admin` once per dev stack, same pattern as the `document.read`/
    // `folder.read` grant documented in apps/user-ui/e2e/README.md.
    await page.locator(".designer-toolbar input[type='text']").fill(PROCESS_NAME);
    await page.getByRole("button", { name: "Speichern" }).click();

    await expect(page.getByText(/Gespeichert als Version 1\./)).toBeVisible({ timeout: 10_000 });
    await expect(page).toHaveURL(/\/designer\/\?id=\d+/);
    const url = new URL(page.url());
    createdId = Number(url.searchParams.get("id"));
    expect(createdId).toBeGreaterThan(0);

    // Back on the overview list, the new process definition is visible
    // with its saved name.
    await page.getByRole("button", { name: "Zurück zur Liste" }).click();
    await expect(page).toHaveURL("/");
    await expect(page.getByRole("cell", { name: PROCESS_NAME })).toBeVisible();
  });
});
