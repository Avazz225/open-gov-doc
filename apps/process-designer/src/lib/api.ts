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

// Jeder Aufruf geht über das Gateway (3.5): /api/{service_type}/{path} statt
// direkter Backend-Adressen - Registry-Auflösung und Auth-Prüfung passieren
// dort, nicht hier.
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

// Domänengetrennte Admin-Rollen (4.6, P6-S5): systemeigen im Permission
// Service, NICHT als Keycloak-Realm-Rolle (anders als `realm_roles` oben) -
// dieselbe Quelle, die auch das Backend-Gating von `POST/DELETE
// /process-definitions` nutzt (Capability `admin.object_config`, seit P6-S6).
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

// Workflow Service (7.1) - Prozessdefinitionen. Seit P6-S8 ist `name` der
// Prozessfamilien-Schlüssel: mehrere Definitionen mit demselben Namen sind
// Versionen derselben Familie, siehe ADR 0027.
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
): Promise<ProcessDefinitionSummary> {
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
