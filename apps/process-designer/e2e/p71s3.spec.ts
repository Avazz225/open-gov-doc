import { apiLogin, deleteProcessDefinitionViaApi, expect, loginViaUi, test } from "./fixtures";

// P71-S3 (7.1): process-designer completions bundle - three real-browser
// checks for the three sub-items built this session. Separate file from
// designer.spec.ts (that one is the canvas-drawing flow, unrelated to
// these) - same unique-name-per-run isolation strategy.
const PROCESS_NAME = `e2e-p71s3-restore-${Date.now()}`;
const DMN_NAME = `e2e-p71s3-dmn-${Date.now()}`;

test.describe("P71-S3: process-definition restore", () => {
  let createdIds: number[] = [];

  test.afterEach(async () => {
    const { access_token: token } = await apiLogin();
    for (const id of createdIds) {
      await deleteProcessDefinitionViaApi(token, id);
    }
    createdIds = [];
  });

  test("restores a historical version as a new, current version", async ({ page }) => {
    await loginViaUi(page);

    // v1: create via UI (same starter-diagram flow as designer.spec.ts).
    await page.getByRole("button", { name: "Neu erstellen" }).click();
    await page.waitForURL(/\/designer\//);
    await expect(page.locator('.djs-shape[data-element-id="StartEvent_1"]')).toBeVisible();
    await page.locator(".designer-toolbar input[type='text']").fill(PROCESS_NAME);
    await page.getByRole("button", { name: "Speichern" }).click();
    await expect(page.getByText(/Gespeichert als Version 1\./)).toBeVisible({ timeout: 10_000 });
    const v1Id = Number(new URL(page.url()).searchParams.get("id"));
    createdIds.push(v1Id);

    // v2: save again under the same name (append-only versioning, same
    // family) - content doesn't need to differ for this test, only the
    // restore mechanics are under test here.
    await page.getByRole("button", { name: "Speichern" }).click();
    await expect(page.getByText(/Gespeichert als Version 2\./)).toBeVisible({ timeout: 10_000 });
    const v2Id = Number(new URL(page.url()).searchParams.get("id"));
    createdIds.push(v2Id);

    await page.getByRole("button", { name: "Zurück zur Liste" }).click();
    await expect(page).toHaveURL("/");
    const row = page.getByRole("row", { name: new RegExp(PROCESS_NAME) });
    await expect(row).toBeVisible();
    await expect(row.getByRole("cell", { name: "2", exact: true })).toBeVisible();

    await row.getByRole("button", { name: "Versionen anzeigen" }).click();
    const restoreButtons = page.getByRole("button", { name: "Wiederherstellen" });
    // Only the non-latest version (v1) gets a restore button - v2 (the
    // row already shown as current) must not offer restoring itself.
    await expect(restoreButtons).toHaveCount(1);

    page.once("dialog", (dialog) => dialog.accept());
    await restoreButtons.click();

    // Restoring creates v3 - the family's own version count updates.
    await expect(row.getByRole("cell", { name: "3", exact: true })).toBeVisible({
      timeout: 10_000,
    });
  });
});

test.describe("P71-S3: DMN cross-reference view and design-time validation", () => {
  let dmnId: number | null = null;
  let processId: number | null = null;

  test.afterEach(async () => {
    const { access_token: token } = await apiLogin();
    if (processId !== null) {
      await deleteProcessDefinitionViaApi(token, processId);
      processId = null;
    }
    if (dmnId !== null) {
      await fetch(
        `${process.env.E2E_GATEWAY_BASE_URL || "http://localhost:8009"}/api/workflow-service/dmn-definitions/${dmnId}`,
        { method: "DELETE", headers: { Authorization: `Bearer ${token}` } }
      );
      dmnId = null;
    }
  });

  test("shows no references before use, then the referencing process after", async ({ page }) => {
    const { access_token: token } = await apiLogin();
    const gateway = process.env.E2E_GATEWAY_BASE_URL || "http://localhost:8009";

    const dmnXml = `<?xml version="1.0" encoding="UTF-8"?>
<definitions xmlns="https://www.omg.org/spec/DMN/20191111/MODEL/" id="d1" name="d1" namespace="https://camunda.org/schema/1.0/dmn">
  <decision id="e2e-p71s3-decision" name="E2E Decision">
    <decisionTable id="dt1">
      <input id="i1"><inputExpression id="ie1" typeRef="string"><text>x</text></inputExpression></input>
      <output id="o1" typeRef="string" />
    </decisionTable>
  </decision>
</definitions>`;
    const dmnForm = new FormData();
    dmnForm.set("name", DMN_NAME);
    dmnForm.set("dmn_xml", new Blob([dmnXml], { type: "application/xml" }), "decision.dmn");
    const dmnResponse = await fetch(`${gateway}/api/workflow-service/dmn-definitions`, {
      method: "POST",
      headers: { Authorization: `Bearer ${token}` },
      body: dmnForm,
    });
    const dmn = (await dmnResponse.json()) as { id: number };
    dmnId = dmn.id;

    await loginViaUi(page);
    // The DMN overview list is a tab on the home page, not a separate
    // route (src/app/page.tsx's simple tab switcher).
    await page.getByRole("button", { name: "Entscheidungstabellen (DMN)" }).click();

    const dmnRow = page.getByRole("row", { name: new RegExp(DMN_NAME) });
    await expect(dmnRow).toBeVisible({ timeout: 10_000 });
    await dmnRow.getByRole("button", { name: "Verwendung anzeigen" }).click();
    await expect(page.getByText("Wird von keiner gespeicherten Prozessdefinition referenziert.")).toBeVisible();

    // Now upload a BPMN file referencing this DMN's decision id and
    // confirm the reference view picks it up.
    const businessRuleTaskBpmn = `<?xml version="1.0" encoding="UTF-8"?>
<bpmn:definitions xmlns:bpmn="http://www.omg.org/spec/BPMN/20100524/MODEL"
  xmlns:camunda="http://camunda.org/schema/1.0/bpmn"
  id="Definitions_1" targetNamespace="http://bpmn.io/schema/bpmn">
  <bpmn:process id="Process_e2e_p71s3" isExecutable="true">
    <bpmn:startEvent id="Start_1"><bpmn:outgoing>Flow_1</bpmn:outgoing></bpmn:startEvent>
    <bpmn:businessRuleTask id="BRT_1" name="Decide" camunda:decisionRef="e2e-p71s3-decision">
      <bpmn:incoming>Flow_1</bpmn:incoming>
      <bpmn:outgoing>Flow_2</bpmn:outgoing>
    </bpmn:businessRuleTask>
    <bpmn:sequenceFlow id="Flow_1" sourceRef="Start_1" targetRef="BRT_1" />
    <bpmn:endEvent id="End_1"><bpmn:incoming>Flow_2</bpmn:incoming></bpmn:endEvent>
    <bpmn:sequenceFlow id="Flow_2" sourceRef="BRT_1" targetRef="End_1" />
  </bpmn:process>
</bpmn:definitions>`;
    const bpmnForm = new FormData();
    bpmnForm.set("name", `e2e-p71s3-process-${Date.now()}`);
    bpmnForm.set(
      "bpmn_xml",
      new Blob([businessRuleTaskBpmn], { type: "application/xml" }),
      "process.bpmn"
    );
    const pdResponse = await fetch(`${gateway}/api/workflow-service/process-definitions`, {
      method: "POST",
      headers: { Authorization: `Bearer ${token}` },
      body: bpmnForm,
    });
    const pd = (await pdResponse.json()) as { id: number; name: string };
    processId = pd.id;

    // Still expanded from above - the toggle button now reads "...
    // ausblenden" (hide). Collapse then re-expand to force a refetch.
    await dmnRow.getByRole("button", { name: "Verwendung ausblenden" }).click();
    await dmnRow.getByRole("button", { name: "Verwendung anzeigen" }).click();
    await expect(page.getByText(new RegExp(pd.name))).toBeVisible({ timeout: 10_000 });
  });

  test("warns in the properties panel when a decisionRef matches no loaded DMN family", async ({
    page,
  }) => {
    await loginViaUi(page);
    await page.getByRole("button", { name: "Neu erstellen" }).click();
    await page.waitForURL(/\/designer\//);
    await expect(page.locator('.djs-shape[data-element-id="StartEvent_1"]')).toBeVisible();

    // bpmn-js needs a real `bpmndi:BPMNDiagram` section to render shapes at
    // all - a minimal element-only BPMN file (fine for the backend's
    // text-based `extract_decision_refs`, see repository.py) imports as an
    // empty "no diagram to display" canvas here, since bpmn-js does not
    // auto-layout on import.
    const unknownRefBpmn = `<?xml version="1.0" encoding="UTF-8"?>
<bpmn:definitions xmlns:bpmn="http://www.omg.org/spec/BPMN/20100524/MODEL"
  xmlns:bpmndi="http://www.omg.org/spec/BPMN/20100524/DI"
  xmlns:dc="http://www.omg.org/spec/DD/20100524/DC"
  xmlns:camunda="http://camunda.org/schema/1.0/bpmn"
  id="Definitions_1" targetNamespace="http://bpmn.io/schema/bpmn">
  <bpmn:process id="Process_e2e_p71s3_warn" isExecutable="true">
    <bpmn:startEvent id="Start_1"><bpmn:outgoing>Flow_1</bpmn:outgoing></bpmn:startEvent>
    <bpmn:businessRuleTask id="BRT_unknown" name="Decide" camunda:decisionRef="does-not-exist-anywhere">
      <bpmn:incoming>Flow_1</bpmn:incoming>
      <bpmn:outgoing>Flow_2</bpmn:outgoing>
    </bpmn:businessRuleTask>
    <bpmn:sequenceFlow id="Flow_1" sourceRef="Start_1" targetRef="BRT_unknown" />
    <bpmn:endEvent id="End_1"><bpmn:incoming>Flow_2</bpmn:incoming></bpmn:endEvent>
    <bpmn:sequenceFlow id="Flow_2" sourceRef="BRT_unknown" targetRef="End_1" />
  </bpmn:process>
  <bpmndi:BPMNDiagram id="BPMNDiagram_1">
    <bpmndi:BPMNPlane id="BPMNPlane_1" bpmnElement="Process_e2e_p71s3_warn">
      <bpmndi:BPMNShape id="Start_1_di" bpmnElement="Start_1">
        <dc:Bounds x="150" y="100" width="36" height="36" />
      </bpmndi:BPMNShape>
      <bpmndi:BPMNShape id="BRT_unknown_di" bpmnElement="BRT_unknown">
        <dc:Bounds x="250" y="78" width="100" height="80" />
      </bpmndi:BPMNShape>
      <bpmndi:BPMNShape id="End_1_di" bpmnElement="End_1">
        <dc:Bounds x="420" y="100" width="36" height="36" />
      </bpmndi:BPMNShape>
      <bpmndi:BPMNEdge id="Flow_1_di" bpmnElement="Flow_1">
        <di:waypoint xmlns:di="http://www.omg.org/spec/DD/20100524/DI" x="186" y="118" />
        <di:waypoint xmlns:di="http://www.omg.org/spec/DD/20100524/DI" x="250" y="118" />
      </bpmndi:BPMNEdge>
      <bpmndi:BPMNEdge id="Flow_2_di" bpmnElement="Flow_2">
        <di:waypoint xmlns:di="http://www.omg.org/spec/DD/20100524/DI" x="350" y="118" />
        <di:waypoint xmlns:di="http://www.omg.org/spec/DD/20100524/DI" x="420" y="118" />
      </bpmndi:BPMNEdge>
    </bpmndi:BPMNPlane>
  </bpmndi:BPMNDiagram>
</bpmn:definitions>`;

    await page.setInputFiles('input[type="file"]', {
      name: "unknown-ref.bpmn",
      mimeType: "application/xml",
      buffer: Buffer.from(unknownRefBpmn),
    });

    const businessRuleShape = page.locator('.djs-shape[data-element-id="BRT_unknown"]');
    await expect(businessRuleShape).toBeVisible({ timeout: 10_000 });
    await businessRuleShape.click();

    // The group is open by default (`shouldOpen: true`, since a warning
    // group is pointless collapsed) - no click needed to see its content.
    await expect(page.getByText(/Keine geladene DMN-Entscheidungstabelle/)).toBeVisible({
      timeout: 10_000,
    });
    await expect(page.getByText(/does-not-exist-anywhere/)).toBeVisible();
  });
});
