import { GATEWAY_BASE_URL } from "./config";

export class ApiError extends Error {
  status: number;

  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

async function extractErrorMessage(response: Response): Promise<string> {
  try {
    const body = await response.json();
    if (typeof body?.detail === "string") return body.detail;
    return JSON.stringify(body?.detail ?? body);
  } catch {
    return response.statusText || `HTTP ${response.status}`;
  }
}

// Every call goes through the gateway (3.5): /api/{service_type}/{path} instead
// of direct backend addresses - registry resolution and auth checks happen
// there, not here.
async function request(
  serviceType: string,
  path: string,
  init: RequestInit = {},
  token?: string
): Promise<Response> {
  const headers = new Headers(init.headers);
  if (token) headers.set("Authorization", `Bearer ${token}`);

  const response = await fetch(`${GATEWAY_BASE_URL}/api/${serviceType}/${path}`, {
    ...init,
    headers,
  });

  if (!response.ok) {
    throw new ApiError(response.status, await extractErrorMessage(response));
  }
  return response;
}

export interface TokenResponse {
  access_token: string;
  refresh_token: string;
  expires_in: number;
  token_type: string;
}

export async function login(username: string, password: string): Promise<TokenResponse> {
  const response = await request("auth-service", "login", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username, password }),
  });
  return response.json();
}

export async function refreshToken(refresh_token: string): Promise<TokenResponse> {
  const response = await request("auth-service", "refresh", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ refresh_token }),
  });
  return response.json();
}

export interface CurrentUser {
  sub: string;
  username: string;
  email: string | null;
  realm_roles: string[];
}

export async function getCurrentUser(token: string): Promise<CurrentUser> {
  const response = await request("auth-service", "me", {}, token);
  return response.json();
}

// Domain-separated admin roles (4.6, P6-S5): native to the Permission
// Service, NOT a Keycloak realm role (unlike `realm_roles` above) -
// the same source that also backs the backend gating of `POST/DELETE
// /process-definitions` (capability `admin.object_config`, since P6-S6).
export async function getEffectivePermissions(
  token: string,
  principalId: string
): Promise<string[]> {
  const response = await request(
    "permission-service",
    `effective-permissions/${principalId}/root`,
    {},
    token
  );
  const body = (await response.json()) as { permissions: string[] };
  return body.permissions;
}

export type ThemeName = "light" | "dark" | "high-contrast" | "auto";

export async function getThemePreference(token: string): Promise<ThemeName> {
  const response = await request("auth-service", "me/preferences", {}, token);
  const body = (await response.json()) as { theme: ThemeName };
  return body.theme;
}

export async function updateThemePreference(token: string, theme: ThemeName): Promise<void> {
  await request(
    "auth-service",
    "me/preferences",
    {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ theme }),
    },
    token
  );
}

export interface MaintenanceMode {
  active: boolean;
}

export async function getMaintenanceStatus(token: string): Promise<MaintenanceMode> {
  const response = await request("permission-service", "maintenance-mode", {}, token);
  return response.json();
}

// Workflow Service (7.1) - process definitions. Since P6-S8, `name` is the
// process family key: multiple definitions with the same name are
// versions of the same family, see ADR 0027.
export interface ProcessDefinitionSummary {
  id: number;
  name: string;
  version: number;
  bpmn_process_id: string;
  created_at: string;
  updated_at: string;
}

export interface ProcessDefinitionDetail extends ProcessDefinitionSummary {
  bpmn_xml: string;
}

// Post-Roadmap Phase 21 Session 4 (ADR 0087): `POST /process-definitions`
// can optionally be gated by a four-eyes mechanism - the success case
// (default, no approval requirement configured) remains unchanged as
// `ProcessDefinitionSummary` with `201`; a `202` instead signals
// this new, separate form.
export interface ProcessDefinitionImportPending {
  status: "pending_approval";
  approval_request_id: string;
}

export type CreateProcessDefinitionResult = ProcessDefinitionSummary | ProcessDefinitionImportPending;

export function isPendingApproval(
  result: CreateProcessDefinitionResult
): result is ProcessDefinitionImportPending {
  return "status" in result && result.status === "pending_approval";
}

export async function listProcessDefinitions(token: string): Promise<ProcessDefinitionSummary[]> {
  const response = await request("workflow-service", "process-definitions", {}, token);
  return response.json();
}

export async function listProcessDefinitionVersions(
  token: string,
  name: string
): Promise<ProcessDefinitionSummary[]> {
  const query = new URLSearchParams({ name });
  const response = await request(
    "workflow-service",
    `process-definitions?${query.toString()}`,
    {},
    token
  );
  return response.json();
}

export async function getProcessDefinition(
  token: string,
  id: number
): Promise<ProcessDefinitionDetail> {
  const response = await request("workflow-service", `process-definitions/${id}`, {}, token);
  return response.json();
}

export async function createProcessDefinition(
  token: string,
  params: { name: string; bpmnXml: string; processId?: string }
): Promise<CreateProcessDefinitionResult> {
  const formData = new FormData();
  formData.set("name", params.name);
  if (params.processId) formData.set("process_id", params.processId);
  formData.set(
    "bpmn_xml",
    new Blob([params.bpmnXml], { type: "application/xml" }),
    "process.bpmn"
  );
  const response = await request(
    "workflow-service",
    "process-definitions",
    { method: "POST", body: formData },
    token
  );
  return response.json();
}

export async function deleteProcessDefinition(token: string, id: number): Promise<void> {
  await request(
    "workflow-service",
    `process-definitions/${id}`,
    { method: "DELETE" },
    token
  );
}

// DMN 1.3 decision tables (7.1, P14-S4) - same versioning pattern
// as process definitions (`name` is the family key), see
// `models.DmnDefinition`.
export interface DmnDefinitionSummary {
  id: number;
  name: string;
  version: number;
  decision_id: string;
  created_at: string;
  updated_at: string;
}

export interface DmnDefinitionDetail extends DmnDefinitionSummary {
  dmn_xml: string;
}

export async function listDmnDefinitions(token: string): Promise<DmnDefinitionSummary[]> {
  const response = await request("workflow-service", "dmn-definitions", {}, token);
  return response.json();
}

export async function listDmnDefinitionVersions(
  token: string,
  name: string
): Promise<DmnDefinitionSummary[]> {
  const query = new URLSearchParams({ name });
  const response = await request(
    "workflow-service",
    `dmn-definitions?${query.toString()}`,
    {},
    token
  );
  return response.json();
}

export async function getDmnDefinition(token: string, id: number): Promise<DmnDefinitionDetail> {
  const response = await request("workflow-service", `dmn-definitions/${id}`, {}, token);
  return response.json();
}

export async function createDmnDefinition(
  token: string,
  params: { name: string; dmnXml: string }
): Promise<DmnDefinitionSummary> {
  const formData = new FormData();
  formData.set("name", params.name);
  formData.set(
    "dmn_xml",
    new Blob([params.dmnXml], { type: "application/xml" }),
    "decision.dmn"
  );
  const response = await request(
    "workflow-service",
    "dmn-definitions",
    { method: "POST", body: formData },
    token
  );
  return response.json();
}

export async function deleteDmnDefinition(token: string, id: number): Promise<void> {
  await request("workflow-service", `dmn-definitions/${id}`, { method: "DELETE" }, token);
}

// Federation Hub (7.4, P6-S9) - proxy from workflow-service to the
// hub's address book (`GET /federation/installations`, ungated like other
// GETs). Empty list without a configured hub - the designer then
// doesn't even offer federated process steps (see
// components/FederatedStepPropertiesProvider.tsx).
export interface FederationInstallationSummary {
  id: string;
  display_name: string;
}

export async function listFederationInstallations(
  token: string
): Promise<FederationInstallationSummary[]> {
  const response = await request("workflow-service", "federation/installations", {}, token);
  return response.json();
}
