import {
  apiLogin,
  deleteProcessDefinitionViaApi,
  expect,
  GATEWAY_BASE_URL,
  loginViaUi,
  test,
} from "./fixtures";

// P93-S1 (ADR 0249): binding a lane to a group in the properties panel. A
// definition with one unbound lane is uploaded through the API, opened in
// the designer, the lane bound to a fresh group, saved - and the stored
// version carries the binding. Group and definitions are removed afterwards.
const SHOTS = process.env.E2E_SCREENSHOT_DIR;

function laneXml(): string {
  return `<?xml version="1.0" encoding="UTF-8"?>
<bpmn:definitions xmlns:bpmn="http://www.omg.org/spec/BPMN/20100524/MODEL" xmlns:bpmndi="http://www.omg.org/spec/BPMN/20100524/DI" xmlns:dc="http://www.omg.org/spec/DD/20100524/DC" xmlns:di="http://www.omg.org/spec/DD/20100524/DI" xmlns:camunda="http://camunda.org/schema/1.0/bpmn" id="Definitions_lane_e2e" targetNamespace="http://bpmn.io/schema/bpmn">
  <bpmn:collaboration id="Collaboration_1">
    <bpmn:participant id="Participant_1" name="Antrag" processRef="lane_e2e" />
  </bpmn:collaboration>
  <bpmn:process id="lane_e2e" isExecutable="true">
    <bpmn:laneSet id="LaneSet_1">
      <bpmn:lane id="Lane_1" name="Sachbearbeitung">
        <bpmn:flowNodeRef>StartEvent_1</bpmn:flowNodeRef>
        <bpmn:flowNodeRef>Task_1</bpmn:flowNodeRef>
        <bpmn:flowNodeRef>EndEvent_1</bpmn:flowNodeRef>
      </bpmn:lane>
    </bpmn:laneSet>
    <bpmn:startEvent id="StartEvent_1"><bpmn:outgoing>Flow_1</bpmn:outgoing></bpmn:startEvent>
    <bpmn:manualTask id="Task_1" name="Prüfen"><bpmn:incoming>Flow_1</bpmn:incoming><bpmn:outgoing>Flow_2</bpmn:outgoing></bpmn:manualTask>
    <bpmn:endEvent id="EndEvent_1"><bpmn:incoming>Flow_2</bpmn:incoming></bpmn:endEvent>
    <bpmn:sequenceFlow id="Flow_1" sourceRef="StartEvent_1" targetRef="Task_1" />
    <bpmn:sequenceFlow id="Flow_2" sourceRef="Task_1" targetRef="EndEvent_1" />
  </bpmn:process>
  <bpmndi:BPMNDiagram id="Diagram_1">
    <bpmndi:BPMNPlane id="Plane_1" bpmnElement="Collaboration_1">
      <bpmndi:BPMNShape id="Participant_1_di" bpmnElement="Participant_1" isHorizontal="true">
        <dc:Bounds x="100" y="80" width="600" height="200" />
      </bpmndi:BPMNShape>
      <bpmndi:BPMNShape id="Lane_1_di" bpmnElement="Lane_1" isHorizontal="true">
        <dc:Bounds x="130" y="80" width="570" height="200" />
      </bpmndi:BPMNShape>
      <bpmndi:BPMNShape id="StartEvent_1_di" bpmnElement="StartEvent_1"><dc:Bounds x="200" y="162" width="36" height="36" /></bpmndi:BPMNShape>
      <bpmndi:BPMNShape id="Task_1_di" bpmnElement="Task_1"><dc:Bounds x="320" y="140" width="100" height="80" /></bpmndi:BPMNShape>
      <bpmndi:BPMNShape id="EndEvent_1_di" bpmnElement="EndEvent_1"><dc:Bounds x="520" y="162" width="36" height="36" /></bpmndi:BPMNShape>
      <bpmndi:BPMNEdge id="Flow_1_di" bpmnElement="Flow_1"><di:waypoint x="236" y="180" /><di:waypoint x="320" y="180" /></bpmndi:BPMNEdge>
      <bpmndi:BPMNEdge id="Flow_2_di" bpmnElement="Flow_2"><di:waypoint x="420" y="180" /><di:waypoint x="520" y="180" /></bpmndi:BPMNEdge>
    </bpmndi:BPMNPlane>
  </bpmndi:BPMNDiagram>
</bpmn:definitions>`;
}

test("binds a lane to a group in the properties panel", async ({ page }) => {
  const { access_token: token } = await apiLogin();
  const auth = { Authorization: `Bearer ${token}` };
  const groupName = `E2E-Lane-${Date.now()}`;
  const group = await (
    await fetch(`${GATEWAY_BASE_URL}/api/permission-service/groups`, {
      method: "POST",
      headers: { ...auth, "Content-Type": "application/json" },
      body: JSON.stringify({ name: groupName, description: "e2e" }),
    })
  ).json();
  const form = new FormData();
  form.append("name", `e2e-lane-${Date.now()}`);
  form.append("bpmn_xml", new Blob([laneXml()], { type: "application/xml" }), "lane.bpmn");
  const uploaded = await fetch(`${GATEWAY_BASE_URL}/api/workflow-service/process-definitions`, {
    method: "POST",
    headers: auth,
    body: form,
  });
  expect(uploaded.status).toBe(201);
  const first = (await uploaded.json()) as { id: number; lane_bindings: object };
  expect(first.lane_bindings).toEqual({});
  const created: number[] = [first.id];
  try {
    await loginViaUi(page);
    await page.goto(`/designer/?id=${first.id}`);
    const laneShape = page.locator('.djs-element[data-element-id="Lane_1"]');
    await expect(laneShape).toBeVisible({ timeout: 30_000 });
    // Inside the lane but away from its children selects the lane itself.
    await laneShape.click({ position: { x: 300, y: 20 } });

    const groupHeader = page.locator(".bio-properties-panel-group-header", {
      hasText: "Zuständigkeit (Lane)",
    });
    await expect(groupHeader).toBeVisible();
    await groupHeader.click();
    const select = page.locator("select#bio-properties-panel-laneBinding");
    await select.selectOption({ label: `Gruppe: ${groupName}` });
    if (SHOTS) await page.screenshot({ path: `${SHOTS}/lane-binding-panel-1400.png` });

    await page.getByRole("button", { name: "Speichern" }).click();
    await expect(page.getByText(/Gespeichert als Version 2\./)).toBeVisible({ timeout: 10_000 });
    const savedId = Number(new URL(page.url()).searchParams.get("id"));
    created.push(savedId);

    const saved = await (
      await fetch(`${GATEWAY_BASE_URL}/api/workflow-service/process-definitions/${savedId}`, {
        headers: auth,
      })
    ).json();
    expect(saved.lane_bindings).toEqual({ Sachbearbeitung: { group: groupName } });
    expect(saved.bpmn_xml).toContain(`name="dmsGroup" value="${groupName}"`);
  } finally {
    for (const id of created) await deleteProcessDefinitionViaApi(token, id);
    await fetch(`${GATEWAY_BASE_URL}/api/permission-service/groups/${group.id}`, {
      method: "DELETE",
      headers: auth,
    });
  }
});
