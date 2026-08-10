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

// Anders als admin-ui/process-designer wertet diese App keine domänengetrennte
// Admin-Rolle aus (weder Task-Abschluss noch Vier-Augen-Entscheidungen sind
// backend-seitig capability-gegated, siehe docs/services/reviewer-ui.md
// "Autorisierung") - trotzdem abgerufen, falls eine spätere Session gezielt
// einzelne Aktionen einschränken will, ohne die Auth-Schicht anfassen zu
// müssen (identisches Muster wie process-designer/admin-ui/user-ui).
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

// --- Workflow Engine (7.1) - Freigabeaufgaben (Manual/Signature Tasks) -----
//
// `GET /tasks` (workflow-service, seit P14-S2) listet die bereiten Tasks über
// ALLE laufenden Instanzen hinweg - vorher gab es nur die instanzgebundene
// `GET /instances/{id}/tasks`, für eine Reviewer-Inbox ungeeignet. Federierte
// Tasks (7.4) sind darin bereits serverseitig herausgefiltert (werden
// automatisch über den Federation Hub abgeschlossen, nie von einem Menschen).
export interface ReadyTaskWithInstance {
  id: string;
  name: string;
  lane: string | null;
  data: Record<string, unknown>;
  extensions: Record<string, string>;
  instance_id: string;
  process_definition_id: number;
  business_key: string | null;
}

export async function listReadyTasks(token: string): Promise<ReadyTaskWithInstance[]> {
  const response = await request("workflow-service", "tasks", {}, token);
  return response.json();
}

export async function completeTask(
  token: string,
  params: {
    instanceId: string;
    taskId: string;
    completedBy: string;
    data?: Record<string, unknown>;
    signatureId?: string;
  }
): Promise<void> {
  await request(
    "workflow-service",
    `instances/${params.instanceId}/tasks/${params.taskId}/complete`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        completed_by: params.completedBy,
        data: params.data ?? {},
        signature_id: params.signatureId ?? null,
      }),
    },
    token
  );
}

// --- Permission Service (4.3) - generische Vier-Augen-Freigaben ------------
//
// `permission-service`s `/approval-requests` ist bereits vollständig generisch
// über ALLE `action_type`s hinweg (Dokument-/Ordnerlöschung, Superuser-
// Aktivierung, Bereichssperren, Migrationstransfers, ...) - bislang gab es nur
// je Aufrufer eng gefilterte Konsumenten (admin-ui/user-ui, siehe
// docs/services/reviewer-ui.md), diese App ist der erste generische, alle
// Aktionstypen gleichermaßen zeigende Konsument.
export interface ApprovalRequest {
  id: string;
  action_type: string;
  initiated_by: string;
  payload: Record<string, unknown>;
  status: "pending" | "approved" | "rejected";
  approved_by: string | null;
  rejected_by: string | null;
  reason: string | null;
  created_at: string;
  decided_at: string | null;
}

export async function listApprovalRequests(
  token: string,
  params?: { status?: string }
): Promise<ApprovalRequest[]> {
  const query = params?.status ? `?status=${encodeURIComponent(params.status)}` : "";
  const response = await request("permission-service", `approval-requests${query}`, {}, token);
  return response.json();
}

export async function approveRequest(
  token: string,
  requestId: string,
  approvedBy: string
): Promise<ApprovalRequest> {
  const response = await request(
    "permission-service",
    `approval-requests/${requestId}/approve`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ approved_by: approvedBy }),
    },
    token
  );
  return response.json();
}

export async function rejectRequest(
  token: string,
  requestId: string,
  params: { rejectedBy: string; reason?: string }
): Promise<ApprovalRequest> {
  const response = await request(
    "permission-service",
    `approval-requests/${requestId}/reject`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ rejected_by: params.rejectedBy, reason: params.reason ?? null }),
    },
    token
  );
  return response.json();
}
