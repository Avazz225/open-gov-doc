import {
  expect,
  GATEWAY_BASE_URL,
  loginViaUi,
  test,
  TEST_PASSWORD,
  TEST_USERNAME,
} from "./fixtures";

// P93-S2 (ADR 0249 Part A c) against the running stack: a three-step process
// gets a second version with an extra step; the instance is migrated after a
// positive preview, then sent back to its first step, then that step is
// skipped. `users-admin` gets `domain-admin-workflow-instances` (and
// `domain-admin-config` to upload the two versions) for the test's duration.
const SHOTS = process.env.E2E_SCREENSHOT_DIR;
const WORKFLOW = `${GATEWAY_BASE_URL}/api/workflow-service`;

const V1 = `<?xml version="1.0" encoding="UTF-8"?>
<bpmn:definitions xmlns:bpmn="http://www.omg.org/spec/BPMN/20100524/MODEL" id="Definitions_e2e_ops" targetNamespace="http://bpmn.io/schema/bpmn">
  <bpmn:process id="Process_e2e_ops" isExecutable="true">
    <bpmn:startEvent id="StartEvent_1"><bpmn:outgoing>Flow_1</bpmn:outgoing></bpmn:startEvent>
    <bpmn:sequenceFlow id="Flow_1" sourceRef="StartEvent_1" targetRef="Task_Pruefen" />
    <bpmn:manualTask id="Task_Pruefen" name="Prüfen"><bpmn:incoming>Flow_1</bpmn:incoming><bpmn:outgoing>Flow_2</bpmn:outgoing></bpmn:manualTask>
    <bpmn:sequenceFlow id="Flow_2" sourceRef="Task_Pruefen" targetRef="Task_Freigeben" />
    <bpmn:manualTask id="Task_Freigeben" name="Freigeben"><bpmn:incoming>Flow_2</bpmn:incoming><bpmn:outgoing>Flow_3</bpmn:outgoing></bpmn:manualTask>
    <bpmn:sequenceFlow id="Flow_3" sourceRef="Task_Freigeben" targetRef="Task_Ablegen" />
    <bpmn:manualTask id="Task_Ablegen" name="Ablegen"><bpmn:incoming>Flow_3</bpmn:incoming><bpmn:outgoing>Flow_4</bpmn:outgoing></bpmn:manualTask>
    <bpmn:sequenceFlow id="Flow_4" sourceRef="Task_Ablegen" targetRef="EndEvent_1" />
    <bpmn:endEvent id="EndEvent_1"><bpmn:incoming>Flow_4</bpmn:incoming></bpmn:endEvent>
  </bpmn:process>
</bpmn:definitions>`;

// Version 2 inserts "Gegenlesen" between "Freigeben" and "Ablegen".
const V2 = V1.replace(
  '<bpmn:sequenceFlow id="Flow_3" sourceRef="Task_Freigeben" targetRef="Task_Ablegen" />',
  '<bpmn:sequenceFlow id="Flow_3" sourceRef="Task_Freigeben" targetRef="Task_Gegenlesen" />' +
    '<bpmn:manualTask id="Task_Gegenlesen" name="Gegenlesen"><bpmn:incoming>Flow_3</bpmn:incoming><bpmn:outgoing>Flow_5</bpmn:outgoing></bpmn:manualTask>' +
    '<bpmn:sequenceFlow id="Flow_5" sourceRef="Task_Gegenlesen" targetRef="Task_Ablegen" />'
).replace(
  "<bpmn:incoming>Flow_3</bpmn:incoming><bpmn:outgoing>Flow_4</bpmn:outgoing>",
  "<bpmn:incoming>Flow_5</bpmn:incoming><bpmn:outgoing>Flow_4</bpmn:outgoing>"
);

test("process instances: migrate, send back and skip", async ({ page, request }) => {
  test.setTimeout(120_000);
  const login = await request.post(`${GATEWAY_BASE_URL}/api/auth-service/login`, {
    data: { username: TEST_USERNAME, password: TEST_PASSWORD },
  });
  const auth = { Authorization: `Bearer ${(await login.json()).access_token}` };
  const roles = await (
    await request.get(`${GATEWAY_BASE_URL}/api/permission-service/roles`, { headers: auth })
  ).json();
  const assignments: string[] = [];
  for (const name of ["domain-admin-workflow-instances", "domain-admin-config"]) {
    const roleId = roles.find((r: { name: string }) => r.name === name).id;
    const grant = await request.post(
      `${GATEWAY_BASE_URL}/api/permission-service/role-assignments`,
      {
        headers: auth,
        data: { principal_type: "user", principal_id: "2", role_id: roleId, resource_id: "root" },
      }
    );
    if (grant.ok()) assignments.push((await grant.json()).role_assignment.id);
  }

  const family = `E2E-Eingriff-${Date.now()}`;
  const businessKey = `AZ-${Date.now()}`;
  const upload = async (xml: string) => {
    const response = await request.post(`${WORKFLOW}/process-definitions`, {
      headers: auth,
      multipart: {
        name: family,
        bpmn_xml: { name: "ops.bpmn", mimeType: "application/xml", buffer: Buffer.from(xml) },
      },
    });
    expect(response.status(), await response.text()).toBe(201);
    return (await response.json()) as { id: number; version: number };
  };
  const tasks = async (instanceId: string) =>
    (await (
      await request.get(`${WORKFLOW}/instances/${instanceId}/tasks`, { headers: auth })
    ).json()) as {
      id: string;
      name: string;
    }[];
  const complete = async (instanceId: string, taskId: string) =>
    expect(
      (
        await request.post(`${WORKFLOW}/instances/${instanceId}/tasks/${taskId}/complete`, {
          headers: auth,
          data: {},
        })
      ).status()
    ).toBe(200);

  const v1 = await upload(V1);
  const started = await request.post(`${WORKFLOW}/process-definitions/${v1.id}/instances`, {
    headers: auth,
    data: { business_key: businessKey },
  });
  expect(started.status(), await started.text()).toBe(201);
  const instanceId = (await started.json()).id as string;
  try {
    await complete(instanceId, (await tasks(instanceId))[0].id); // now at "Freigeben"
    await upload(V2);

    await loginViaUi(page);
    await page.goto("/workflow-instances/");
    await page.getByLabel("Vorgang").fill(businessKey);
    await page.getByRole("button", { name: "Suchen" }).click();
    await page.getByRole("button", { name: businessKey }).click();
    const detail = page.getByRole("region", { name: "Instanzdetails" });
    await expect(detail.getByText("Freigeben", { exact: true })).toBeVisible({ timeout: 30_000 });

    // Migrate after a positive preview.
    await detail.getByLabel("Begründung").fill("Vier-Augen-Prinzip ergänzt");
    await detail.getByRole("button", { name: "Vorschau" }).click();
    const report = detail.getByLabel("Migrationsvorschau");
    await expect(report).toContainText("Migration möglich");
    await expect(report).toContainText("Gegenlesen");
    for (const [width, height] of [
      [1400, 900],
      [768, 1000],
      [390, 844],
    ] as const) {
      await page.setViewportSize({ width, height });
      await expect
        .poll(() => page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth))
        .toBeLessThanOrEqual(0);
      if (SHOTS) {
        await detail.scrollIntoViewIfNeeded();
        await page.screenshot({
          path: `${SHOTS}/admin-workflow-instances-${width}.png`,
          fullPage: true,
        });
      }
    }
    await page.setViewportSize({ width: 1400, height: 900 });
    await detail.getByRole("button", { name: "Migrieren" }).click();
    await expect(detail.getByRole("status").first()).toContainText(`Migriert auf ${family} v2.`);
    await expect(detail.getByText("Es gibt keine neuere Version dieses Prozesses.")).toBeVisible();

    // Send back to "Prüfen".
    await detail.getByLabel("Begründung").fill("Unterlagen unvollständig");
    // The only completed task after the migration is "Prüfen", preselected.
    await expect(detail.getByLabel("An Aufgabe")).toContainText("Prüfen");
    await detail.getByRole("button", { name: "Zurücksenden" }).click();
    await expect(
      detail.getByText(/Zurückgesendet an „Prüfen“\. Jetzt offen: Prüfen\./)
    ).toBeVisible();

    // Skip "Prüfen".
    await detail.getByLabel("Begründung").fill("Telefonisch geklärt");
    await detail
      .getByRole("listitem")
      .filter({ hasText: "Prüfen" })
      .getByRole("button", { name: "Überspringen" })
      .click();
    await expect(
      detail.getByText(/„Prüfen“ übersprungen\. Jetzt offen: Freigeben\./)
    ).toBeVisible();
    if (SHOTS)
      await page.screenshot({
        path: `${SHOTS}/admin-workflow-instances-after-skip.png`,
        fullPage: true,
      });

    // Terminate (P95-S1c): ends the instance for good.
    await detail.getByLabel("Begründung").fill("Test beendet");
    page.once("dialog", (dialog) => dialog.accept());
    await detail.getByRole("button", { name: "Instanz beenden" }).click();
    await expect(detail.getByText(/Instanz beendet\. Die Instanz ist beendet\./)).toBeVisible();

    const instance = await (
      await request.get(`${WORKFLOW}/instances/${instanceId}`, { headers: auth })
    ).json();
    expect(instance.process_definition_id).not.toBe(v1.id);
    expect(instance.status).toBe("terminated");
  } finally {
    // Finish the instance so it doesn't linger in the running list.
    for (let i = 0; i < 6; i++) {
      const open = await tasks(instanceId);
      if (!open.length) break;
      await complete(instanceId, open[0].id);
    }
    for (const id of assignments) {
      await request.delete(`${GATEWAY_BASE_URL}/api/permission-service/role-assignments/${id}`, {
        headers: auth,
      });
    }
  }
});
