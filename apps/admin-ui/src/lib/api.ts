import { GATEWAY_BASE_URL as DEFAULT_GATEWAY_BASE_URL } from "./config";

// Mutable statt eines festen Imports (P4-S5, Multi-Installation, Konzept 8):
// Die Admin-UI kann mehrere Installationen mit je eigenem Gateway-Endpunkt
// verwalten - `InstallationProvider` ruft `setGatewayBaseUrl()` bei jedem
// Installationswechsel auf. Alle bestehenden Aufrufer dieses Moduls bleiben
// unverändert (sie kennen die URL nicht, nur den `service_type`/Pfad).
let gatewayBaseUrl = DEFAULT_GATEWAY_BASE_URL;

export function setGatewayBaseUrl(url: string): void {
  gatewayBaseUrl = url;
}

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

  const response = await fetch(`${gatewayBaseUrl}/api/${serviceType}/${path}`, {
    ...init,
    headers,
  });

  if (!response.ok) {
    throw new ApiError(response.status, await extractErrorMessage(response));
  }
  return response;
}

function jsonInit(body: unknown): RequestInit {
  return { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) };
}

export interface TokenResponse {
  access_token: string;
  refresh_token: string;
  expires_in: number;
  token_type: string;
}

export async function login(username: string, password: string): Promise<TokenResponse> {
  const response = await request("auth-service", "login", jsonInit({ username, password }));
  return response.json();
}

export async function refreshToken(refresh_token: string): Promise<TokenResponse> {
  const response = await request("auth-service", "refresh", jsonInit({ refresh_token }));
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
// dieselbe Quelle, die auch das Backend-Gating (z. B. Auth-Service `/users`)
// nutzt, siehe ADR 0023.
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

export interface KeycloakUser {
  id: string;
  username: string;
  email: string | null;
  enabled: boolean;
  first_name: string | null;
  last_name: string | null;
}

export async function listUsers(token: string): Promise<KeycloakUser[]> {
  const response = await request("auth-service", "users", {}, token);
  return response.json();
}

export async function createUser(
  token: string,
  params: { username: string; email: string; password: string; firstName: string; lastName: string }
): Promise<KeycloakUser> {
  const response = await request(
    "auth-service",
    "users",
    jsonInit({
      username: params.username,
      email: params.email,
      password: params.password,
      first_name: params.firstName,
      last_name: params.lastName,
    }),
    token
  );
  return response.json();
}

export async function deleteUser(token: string, userId: string): Promise<void> {
  await request("auth-service", `users/${encodeURIComponent(userId)}`, { method: "DELETE" }, token);
}

export interface Role {
  id: number;
  name: string;
  description: string;
  permissions: string[];
}

export async function listRoles(token: string): Promise<Role[]> {
  const response = await request("permission-service", "roles", {}, token);
  return response.json();
}

export async function createRole(
  token: string,
  params: { name: string; description: string; permissions: string[] }
): Promise<Role> {
  const response = await request("permission-service", "roles", jsonInit(params), token);
  return response.json();
}

export interface RoleAssignment {
  id: number;
  principal_type: string;
  principal_id: string;
  role_id: number;
  resource_id: string;
}

export async function listRoleAssignments(token: string): Promise<RoleAssignment[]> {
  const response = await request("permission-service", "role-assignments", {}, token);
  return response.json();
}

export async function createRoleAssignment(
  token: string,
  params: { principalType: string; principalId: string; roleId: number; resourceId: string }
): Promise<RoleAssignment> {
  const response = await request(
    "permission-service",
    "role-assignments",
    jsonInit({
      principal_type: params.principalType,
      principal_id: params.principalId,
      role_id: params.roleId,
      resource_id: params.resourceId,
    }),
    token
  );
  return response.json();
}

export async function deleteRoleAssignment(token: string, assignmentId: number): Promise<void> {
  await request(
    "permission-service",
    `role-assignments/${assignmentId}`,
    { method: "DELETE" },
    token
  );
}

// Verfügbare Attribut-Typen der Constraint Engine (4.5) - siehe
// libs/dms-constraint-engine.
export type AttributeType = "string" | "decimal" | "integer" | "boolean" | "date" | "reference";

export interface ObjectTypeAttribute {
  name: string;
  type: AttributeType;
  required?: boolean;
  pattern?: string;
  min?: number;
  max?: number;
}

// Sentinel für "direkt unter der Wurzel platzierbar" (2.2a, ADR 0013) - muss
// exakt zum Backend-Konstanten `ROOT_PARENT_TYPE` passen.
export const ROOT_PARENT_TYPE = "$ROOT";

export interface ObjectType {
  id: number;
  name: string;
  applies_to: string;
  attributes: ObjectTypeAttribute[];
  naming_constraints: Record<string, unknown> | null;
  conditions: Array<Record<string, unknown>>;
  allowed_parent_types: string[] | null;
  icon: string | null;
  // Kennzeichengenerator (2.2, seit P5e-S1/S3) - beide nur für
  // applies_to="document" gesetzt. kennzeichen_display_override ist ein
  // Tri-State: null = globaler Standard (KennzeichenConfig) gilt.
  kennzeichen_format: string | null;
  kennzeichen_display_override: boolean | null;
  // Mindest-Signaturniveau (3.10, seit P6-S7) - nur für applies_to="document"
  // gesetzt, null = keine Anforderung. Wird vom Signature Service bei jedem
  // Signiervorgang durchgesetzt, hier nur konfiguriert.
  required_signature_level: "ses" | "aes" | "qes" | null;
}

export async function listObjectTypes(token: string): Promise<ObjectType[]> {
  const response = await request("object-type-service", "object-types", {}, token);
  return response.json();
}

export async function createObjectType(
  token: string,
  params: {
    name: string;
    appliesTo: "document" | "folder";
    attributes: ObjectTypeAttribute[];
    allowedParentTypes: string[] | null;
    icon: string | null;
    kennzeichenFormat: string | null;
    kennzeichenDisplayOverride: boolean | null;
    requiredSignatureLevel: "ses" | "aes" | "qes" | null;
  }
): Promise<ObjectType> {
  const response = await request(
    "object-type-service",
    "object-types",
    jsonInit({
      name: params.name,
      applies_to: params.appliesTo,
      attributes: params.attributes,
      allowed_parent_types: params.allowedParentTypes,
      icon: params.icon,
      kennzeichen_format: params.kennzeichenFormat,
      kennzeichen_display_override: params.kennzeichenDisplayOverride,
      required_signature_level: params.requiredSignatureLevel,
    }),
    token
  );
  return response.json();
}

// Bewusst kein `name`/`appliesTo` im Payload - beide sind serverseitig nach
// Anlage unveränderlich (siehe object-type-service). `namingConstraints`/
// `conditions` werden unverändert durchgereicht statt in der geführten
// Oberfläche editierbar zu sein (außerhalb des Umfangs von P5b-S3) - ohne das
// würde ein Speichern über diesen Editor sie stillschweigend auf ihren
// Default zurücksetzen.
export async function updateObjectType(
  token: string,
  objectTypeId: number,
  params: {
    attributes: ObjectTypeAttribute[];
    namingConstraints: Record<string, unknown> | null;
    conditions: Array<Record<string, unknown>>;
    allowedParentTypes: string[] | null;
    icon: string | null;
    kennzeichenFormat: string | null;
    kennzeichenDisplayOverride: boolean | null;
    requiredSignatureLevel: "ses" | "aes" | "qes" | null;
  }
): Promise<ObjectType> {
  const response = await request(
    "object-type-service",
    `object-types/${objectTypeId}`,
    {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        attributes: params.attributes,
        naming_constraints: params.namingConstraints,
        conditions: params.conditions,
        allowed_parent_types: params.allowedParentTypes,
        icon: params.icon,
        kennzeichen_format: params.kennzeichenFormat,
        kennzeichen_display_override: params.kennzeichenDisplayOverride,
        required_signature_level: params.requiredSignatureLevel,
      }),
    },
    token
  );
  return response.json();
}

export async function deleteObjectType(token: string, objectTypeId: number): Promise<void> {
  await request(
    "object-type-service",
    `object-types/${objectTypeId}`,
    { method: "DELETE" },
    token
  );
}

// Formular-Layouts (2.2b, seit P5b-S2, ADR 0014) - `is_custom: false` heißt
// "generiertes Smart Layout, nicht gespeichert", `true` heißt "explizites,
// über PUT gespeichertes Override".
export type LayoutPurpose = "display" | "search" | "upload";

export interface LayoutField {
  attribute: string;
  label: string;
  required: boolean;
}

export interface LayoutRow {
  columns: LayoutField[];
}

export interface LayoutData {
  rows: LayoutRow[];
  responsive_breakpoint_px: number;
  is_custom: boolean;
}

export async function getObjectTypeLayout(
  token: string,
  objectTypeId: number,
  purpose: LayoutPurpose
): Promise<LayoutData> {
  const response = await request(
    "object-type-service",
    `object-types/${objectTypeId}/layouts/${purpose}`,
    {},
    token
  );
  return response.json();
}

export async function putObjectTypeLayout(
  token: string,
  objectTypeId: number,
  purpose: LayoutPurpose,
  payload: { rows: LayoutRow[]; responsiveBreakpointPx: number }
): Promise<LayoutData> {
  const response = await request(
    "object-type-service",
    `object-types/${objectTypeId}/layouts/${purpose}`,
    {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        rows: payload.rows,
        responsive_breakpoint_px: payload.responsiveBreakpointPx,
      }),
    },
    token
  );
  return response.json();
}

export async function resetObjectTypeLayout(
  token: string,
  objectTypeId: number,
  purpose: LayoutPurpose
): Promise<void> {
  await request(
    "object-type-service",
    `object-types/${objectTypeId}/layouts/${purpose}`,
    { method: "DELETE" },
    token
  );
}

export interface OcrConfig {
  max_word_count: number | null;
  batch_size: number;
  allowed_content_types: string[];
  updated_at: string;
}

// ocrEnabled selbst ist bewusst kein Feld hier - das ist ein Docker-Compose-
// Profil-Opt-out (ADR 0016, P5b-S5), keine Laufzeit-Einstellung des Service.
// Ist ocr-service nicht deployt, schlägt dieser Aufruf mit einem
// Verbindungsfehler fehl - `OcrSettings.tsx` zeigt das dann als "nicht
// erreichbar" statt eines Werts an.
export async function getOcrConfig(token: string): Promise<OcrConfig> {
  const response = await request("ocr-service", "config", {}, token);
  return response.json();
}

export async function updateOcrConfig(
  token: string,
  payload: { maxWordCount: number | null; batchSize: number; allowedContentTypes: string[] }
): Promise<OcrConfig> {
  const response = await request(
    "ocr-service",
    "config",
    {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        max_word_count: payload.maxWordCount,
        batch_size: payload.batchSize,
        allowed_content_types: payload.allowedContentTypes,
      }),
    },
    token
  );
  return response.json();
}

export interface UploadConfig {
  allowed_content_types: string[];
  updated_at: string;
}

// Analog zu getOcrConfig/updateOcrConfig - Document Service, nicht OCR Service
// (P5d-S1, Format-Whitelist statt OCR-Filterliste).
export async function getUploadConfig(token: string): Promise<UploadConfig> {
  const response = await request("document-service", "upload-config", {}, token);
  return response.json();
}

export async function updateUploadConfig(
  token: string,
  payload: { allowedContentTypes: string[] }
): Promise<UploadConfig> {
  const response = await request(
    "document-service",
    "upload-config",
    {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ allowed_content_types: payload.allowedContentTypes }),
    },
    token
  );
  return response.json();
}

export interface KennzeichenConfig {
  show_before_filename: boolean;
  updated_at: string;
}

// Globaler Anzeige-Standard des Kennzeichengenerators (2.2, seit P5e-S3) -
// lebt im Object-Type Service, nicht im Document Service, da dort auch schon
// der per-Objekttyp-Override (`ObjectType.kennzeichen_display_override`) sitzt.
export async function getKennzeichenConfig(token: string): Promise<KennzeichenConfig> {
  const response = await request("object-type-service", "kennzeichen-config", {}, token);
  return response.json();
}

export async function updateKennzeichenConfig(
  token: string,
  payload: { showBeforeFilename: boolean }
): Promise<KennzeichenConfig> {
  const response = await request(
    "object-type-service",
    "kennzeichen-config",
    {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ show_before_filename: payload.showBeforeFilename }),
    },
    token
  );
  return response.json();
}

export interface GuardConfig {
  allow_degraded_start: boolean;
  updated_at: string;
}

export async function getGuardConfig(token: string): Promise<GuardConfig> {
  const response = await request("storage-service", "guard-config", {}, token);
  return response.json();
}

export async function updateGuardConfig(
  token: string,
  allowDegradedStart: boolean
): Promise<GuardConfig> {
  const response = await request(
    "storage-service",
    "guard-config",
    {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ allow_degraded_start: allowDegradedStart }),
    },
    token
  );
  return response.json();
}

export interface GuardStatusEntry {
  target_id: string;
  device_id: string | null;
  verified_at: string | null;
  pending_copies: number;
}

export async function getGuardStatus(token: string): Promise<GuardStatusEntry[]> {
  const response = await request("storage-service", "guard-status", {}, token);
  return response.json();
}

export async function reidentifyTarget(token: string, targetId: string): Promise<GuardStatusEntry> {
  const response = await request(
    "storage-service",
    `guard-status/${encodeURIComponent(targetId)}/reidentify`,
    { method: "POST" },
    token
  );
  return response.json();
}

export interface ServiceInstance {
  instance_id: string;
  service_type: string;
  version: string;
  address: string;
  healthy: boolean;
  registered_at: string;
  last_heartbeat_at: string;
}

export async function listServiceInstances(token: string): Promise<ServiceInstance[]> {
  const response = await request("registry-service", "instances", {}, token);
  return response.json();
}

// Superuser Break-Glass (4.6, P6-S5) - Aktivierung selbst läuft über den
// bereits bestehenden generischen Vier-Augen-Mechanismus des Permission
// Service (P6-S4, ADR 0022), kein separates Approval-System hier.
export interface SuperuserStatus {
  active: boolean;
  expires_at: string | null;
  principal_id: string | null;
}

export async function getSuperuserStatus(token: string): Promise<SuperuserStatus> {
  const response = await request("auth-service", "superuser/status", {}, token);
  return response.json();
}

export interface ApprovalRequest {
  id: string;
  action_type: string;
  initiated_by: string;
  status: string;
  approved_by: string | null;
  created_at: string;
}

export async function requestSuperuserActivation(
  token: string,
  initiatedBy: string
): Promise<ApprovalRequest> {
  const response = await request(
    "permission-service",
    "approval-requests",
    jsonInit({ action_type: "auth.superuser.activate", initiated_by: initiatedBy, payload: {} }),
    token
  );
  return response.json();
}

export async function listPendingSuperuserActivations(token: string): Promise<ApprovalRequest[]> {
  const response = await request(
    "permission-service",
    "approval-requests?status=pending&action_type=auth.superuser.activate",
    {},
    token
  );
  return response.json();
}

export async function approveApprovalRequest(
  token: string,
  requestId: string,
  approvedBy: string
): Promise<ApprovalRequest> {
  const response = await request(
    "permission-service",
    `approval-requests/${requestId}/approve`,
    jsonInit({ approved_by: approvedBy }),
    token
  );
  return response.json();
}

// Not-Shutdown (4.8, P6-S6) - Auslösung nutzt denselben generischen Vier-
// Augen-Mechanismus wie Break-Glass, hier aber mit einem direkten
// Ausführungspfad (siehe permission-service `POST /maintenance-mode/trigger`),
// falls kein Vier-Augen-Zwischenschritt konfiguriert ist.
export interface MaintenanceMode {
  active: boolean;
  reason: string | null;
  triggered_by: string | null;
  activated_at: string | null;
  lifted_by: string | null;
  lifted_at: string | null;
}

export async function getMaintenanceStatus(token: string): Promise<MaintenanceMode> {
  const response = await request("permission-service", "maintenance-mode", {}, token);
  return response.json();
}

export interface MaintenanceModeActionResult {
  status: "activated" | "pending_approval";
  maintenance_mode: MaintenanceMode | null;
  approval_request_id: string | null;
}

export async function triggerMaintenanceMode(
  token: string,
  triggeredBy: string,
  reason: string
): Promise<MaintenanceModeActionResult> {
  const response = await request(
    "permission-service",
    "maintenance-mode/trigger",
    jsonInit({ triggered_by: triggeredBy, reason: reason || null }),
    token
  );
  return response.json();
}

export async function liftMaintenanceMode(
  token: string,
  liftedBy: string
): Promise<MaintenanceMode> {
  const response = await request(
    "permission-service",
    "maintenance-mode/lift",
    jsonInit({ lifted_by: liftedBy }),
    token
  );
  return response.json();
}
