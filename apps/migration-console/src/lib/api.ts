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

// migration-service gated seine schreibenden Endpunkte nur über die eigene
// Lizenzprüfung (`license_gate`), keine domänengetrennte Admin-Rolle (siehe
// docs/services/migration-console.md "Autorisierung") - trotzdem abgerufen,
// identisches Muster wie reviewer-ui/process-designer/admin-ui/user-ui, falls
// eine spätere Session gezielt einschränken will.
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

// --- Migration/Transfer (7.2) - migration-service ---------------------------

export interface PairedInstallation {
  id: string;
  display_name: string;
  base_url: string;
  created_at: string;
}

export interface PairedInstallationCreated extends PairedInstallation {
  // Nur in der Anlage-Antwort enthalten, nie in GET/List (siehe
  // docs/services/migration-service.md "Paarung") - diese App darf ihn
  // deshalb auch nur unmittelbar nach dem Anlegen einmalig anzeigen.
  api_key: string;
}

export async function listPairedInstallations(token: string): Promise<PairedInstallation[]> {
  const response = await request("migration-service", "paired-installations", {}, token);
  return response.json();
}

export async function createPairedInstallation(
  token: string,
  params: { displayName: string; baseUrl: string; apiKey?: string }
): Promise<PairedInstallationCreated> {
  const response = await request(
    "migration-service",
    "paired-installations",
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        display_name: params.displayName,
        base_url: params.baseUrl,
        api_key: params.apiKey || null,
      }),
    },
    token
  );
  return response.json();
}

export async function deletePairedInstallation(token: string, id: string): Promise<void> {
  await request("migration-service", `paired-installations/${id}`, { method: "DELETE" }, token);
}

export interface Transfer {
  id: string;
  source_folder_id: string;
  target_installation_id: string;
  dry_run: boolean;
  retention_days: number;
  status: string;
  workflow_instance_id: string | null;
  documents_total: number;
  documents_copied: number;
  documents_verified: number;
  error_message: string | null;
  created_by: string;
  created_at: string;
  updated_at: string;
  locked_at: string | null;
  copied_at: string | null;
  verified_at: string | null;
  released_at: string | null;
  deletion_scheduled_at: string | null;
  deleted_at: string | null;
}

export interface TransferStartResult {
  status: string;
  transfer: Transfer | null;
  approval_request_id: string | null;
}

export async function listTransfers(token: string, status?: string): Promise<Transfer[]> {
  const query = status ? `?status=${encodeURIComponent(status)}` : "";
  const response = await request("migration-service", `transfers${query}`, {}, token);
  return response.json();
}

export async function getTransfer(token: string, id: string): Promise<Transfer> {
  const response = await request("migration-service", `transfers/${id}`, {}, token);
  return response.json();
}

export async function createTransfer(
  token: string,
  params: {
    sourceFolderId: string;
    targetInstallationId: string;
    createdBy: string;
    dryRun: boolean;
    retentionDays?: number;
  }
): Promise<TransferStartResult> {
  const response = await request(
    "migration-service",
    "transfers",
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        source_folder_id: params.sourceFolderId,
        target_installation_id: params.targetInstallationId,
        created_by: params.createdBy,
        dry_run: params.dryRun,
        retention_days: params.retentionDays ?? null,
      }),
    },
    token
  );
  return response.json();
}
