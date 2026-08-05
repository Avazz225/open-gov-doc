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
  // Aufbewahrung (5.2, seit P7-S1) - gilt anders als Kennzeichen/Signatur für
  // applies_to="document" UND "folder" gleichermaßen. deletion_reason_
  // required_override ist ein Tri-State wie kennzeichen_display_override:
  // null = installationsweiter Standard (RetentionConfig) gilt.
  default_retention_days: number | null;
  deletion_reason_required_override: boolean | null;
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
    defaultRetentionDays: number | null;
    deletionReasonRequiredOverride: boolean | null;
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
      default_retention_days: params.defaultRetentionDays,
      deletion_reason_required_override: params.deletionReasonRequiredOverride,
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
    defaultRetentionDays: number | null;
    deletionReasonRequiredOverride: boolean | null;
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
        default_retention_days: params.defaultRetentionDays,
        deletion_reason_required_override: params.deletionReasonRequiredOverride,
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

// Aufbewahrung/Legal Hold/Zwangslöschung (5.2/5.2a, seit P7-S1) - gleiches
// Get/Update-Muster wie UploadConfig oben, ebenfalls document-service.
export interface RetentionConfig {
  deletion_reason_required: boolean;
  reminder_lead_days: number | null;
  updated_at: string;
}

export async function getRetentionConfig(token: string): Promise<RetentionConfig> {
  const response = await request("document-service", "retention-config", {}, token);
  return response.json();
}

export async function updateRetentionConfig(
  token: string,
  payload: { deletionReasonRequired: boolean; reminderLeadDays: number | null }
): Promise<RetentionConfig> {
  const response = await request(
    "document-service",
    "retention-config",
    {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        deletion_reason_required: payload.deletionReasonRequired,
        reminder_lead_days: payload.reminderLeadDays,
      }),
    },
    token
  );
  return response.json();
}

export interface TrashConfig {
  restore_period_days: number;
  updated_at: string;
}

export async function getTrashConfig(token: string): Promise<TrashConfig> {
  const response = await request("document-service", "trash-config", {}, token);
  return response.json();
}

export async function updateTrashConfig(
  token: string,
  payload: { restorePeriodDays: number }
): Promise<TrashConfig> {
  const response = await request(
    "document-service",
    "trash-config",
    {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ restore_period_days: payload.restorePeriodDays }),
    },
    token
  );
  return response.json();
}

export interface DeletionRegisterEntry {
  id: string;
  document_id: string;
  trigger: "forced_deletion" | "trash_expiry";
  reason: string | null;
  triggered_by: string | null;
  occurred_at: string;
}

export async function listDeletionRegister(token: string): Promise<DeletionRegisterEntry[]> {
  const response = await request("document-service", "deletion-register", {}, token);
  return response.json();
}

// Aufbewahrung/Legal Hold/Zwangslöschung für Ordner (5.2/5.2a, seit P7-S1b) -
// eigene, unabhängig konfigurierbare Configs (nicht dieselben Zeilen wie
// document-service, siehe docs/services/folder-service.md).
export async function getFolderRetentionConfig(token: string): Promise<RetentionConfig> {
  const response = await request("folder-service", "retention-config", {}, token);
  return response.json();
}

export async function updateFolderRetentionConfig(
  token: string,
  payload: { deletionReasonRequired: boolean; reminderLeadDays: number | null }
): Promise<RetentionConfig> {
  const response = await request(
    "folder-service",
    "retention-config",
    {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        deletion_reason_required: payload.deletionReasonRequired,
        reminder_lead_days: payload.reminderLeadDays,
      }),
    },
    token
  );
  return response.json();
}

export async function getFolderTrashConfig(token: string): Promise<TrashConfig> {
  const response = await request("folder-service", "trash-config", {}, token);
  return response.json();
}

export async function updateFolderTrashConfig(
  token: string,
  payload: { restorePeriodDays: number }
): Promise<TrashConfig> {
  const response = await request(
    "folder-service",
    "trash-config",
    {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ restore_period_days: payload.restorePeriodDays }),
    },
    token
  );
  return response.json();
}

export interface FolderDeletionRegisterEntry {
  id: string;
  folder_id: string;
  trigger: "forced_deletion" | "trash_expiry";
  reason: string | null;
  triggered_by: string | null;
  occurred_at: string;
}

export async function listFolderDeletionRegister(
  token: string
): Promise<FolderDeletionRegisterEntry[]> {
  const response = await request("folder-service", "deletion-register", {}, token);
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
  // Aufbewahrung/WORM (5.1/5.2a, seit P7-S1) - nur lesend, siehe
  // docs/adr/0030-storage-object-lock-governance-mode.md.
  object_lock_mode: "governance" | null;
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

// Standardberichte (5.4a, seit P7-S2b) - vier Berichtstypen, jeweils eine
// JSON-Ansicht + ein Export-Endpunkt (CSV/PDF), plus planbarer Versand.
export interface DocumentVolumeEntry {
  period: string;
  folder_id: string | null;
  count: number;
}

export interface OpenWorkflowTaskEntry {
  instance_id: string;
  process_definition_id: string;
  business_key: string | null;
  task_id: string;
  task_name: string;
  lane: string | null;
}

export interface StorageUsageReportEntry {
  backend: string;
  object_count: number;
  total_size_bytes: number;
}

export interface UserActivityEntry {
  actor: string;
  event_type: string;
  count: number;
}

export type ReportFormat = "csv" | "pdf";
export type ReportFrequency = "daily" | "weekly" | "monthly";
export type ReportType =
  | "document_volume"
  | "open_workflow_tasks"
  | "storage_usage"
  | "user_activity";

export async function getDocumentVolumeReport(
  token: string,
  params: { since?: string; until?: string; folderId?: string; groupBy?: "day" | "week" | "month" }
): Promise<DocumentVolumeEntry[]> {
  const query = new URLSearchParams();
  if (params.since) query.set("since", params.since);
  if (params.until) query.set("until", params.until);
  if (params.folderId) query.set("folder_id", params.folderId);
  if (params.groupBy) query.set("group_by", params.groupBy);
  const response = await request(
    "reporting-service",
    `reports/document-volume?${query.toString()}`,
    {},
    token
  );
  return response.json();
}

export async function getOpenWorkflowTasksReport(token: string): Promise<OpenWorkflowTaskEntry[]> {
  const response = await request("reporting-service", "reports/open-workflow-tasks", {}, token);
  return response.json();
}

export async function getStorageUsageReport(token: string): Promise<StorageUsageReportEntry[]> {
  const response = await request("reporting-service", "reports/storage-usage", {}, token);
  return response.json();
}

export async function getUserActivityReport(
  token: string,
  params: { actor?: string; since?: string; until?: string }
): Promise<UserActivityEntry[]> {
  const query = new URLSearchParams();
  if (params.actor) query.set("actor", params.actor);
  if (params.since) query.set("since", params.since);
  if (params.until) query.set("until", params.until);
  const response = await request(
    "reporting-service",
    `reports/user-activity?${query.toString()}`,
    {},
    token
  );
  return response.json();
}

export async function exportReport(
  token: string,
  reportType: ReportType,
  format: ReportFormat,
  extraParams: Record<string, string | undefined> = {}
): Promise<Blob> {
  const path = reportType.replace(/_/g, "-");
  const query = new URLSearchParams({ format });
  for (const [key, value] of Object.entries(extraParams)) {
    if (value) query.set(key, value);
  }
  const response = await request(
    "reporting-service",
    `reports/${path}/export?${query.toString()}`,
    {},
    token
  );
  return response.blob();
}

export interface ReportSchedule {
  id: string;
  report_type: ReportType;
  format: ReportFormat;
  frequency: ReportFrequency;
  recipient_email: string;
  filters: Record<string, unknown>;
  next_run_at: string;
  last_run_at: string | null;
  created_at: string;
}

export async function listReportSchedules(token: string): Promise<ReportSchedule[]> {
  const response = await request("reporting-service", "report-schedules", {}, token);
  return response.json();
}

export async function createReportSchedule(
  token: string,
  payload: {
    reportType: ReportType;
    format: ReportFormat;
    frequency: ReportFrequency;
    recipientEmail: string;
    filters?: Record<string, unknown>;
  }
): Promise<ReportSchedule> {
  const response = await request(
    "reporting-service",
    "report-schedules",
    jsonInit({
      report_type: payload.reportType,
      format: payload.format,
      frequency: payload.frequency,
      recipient_email: payload.recipientEmail,
      filters: payload.filters ?? {},
    }),
    token
  );
  return response.json();
}

export async function deleteReportSchedule(token: string, scheduleId: string): Promise<void> {
  await request(
    "reporting-service",
    `report-schedules/${encodeURIComponent(scheduleId)}`,
    { method: "DELETE" },
    token
  );
}

// Forensik-Trace (5.4b, seit P7-S2c) - objektbezogene Nachverfolgung
// ("alle Aktionen von Nutzer X"/"alle Nutzer auf Dokument Y") auf Basis der
// P7-S2-Audit-Filter-API, zweite Funktion des reporting-service (5.4).
export type ForensicTraceCategory = "view" | "download" | "change" | "delete";

export interface ForensicTraceEntry {
  id: number;
  event_type: string;
  category: ForensicTraceCategory;
  occurred_at: string;
  service_name: string;
  subject: string | null;
  actor: string | null;
  payload: Record<string, unknown>;
}

export interface ForensicTraceResult {
  entries: ForensicTraceEntry[];
  anomalies: string[];
}

export interface ForensicTraceFilters {
  actor?: string;
  subject?: string;
  eventType?: string;
  category?: ForensicTraceCategory;
  since?: string;
  until?: string;
}

function forensicTraceQuery(
  queriedBy: string,
  filters: ForensicTraceFilters
): URLSearchParams {
  const query = new URLSearchParams({ queried_by: queriedBy });
  if (filters.actor) query.set("actor", filters.actor);
  if (filters.subject) query.set("subject", filters.subject);
  if (filters.eventType) query.set("event_type", filters.eventType);
  if (filters.category) query.set("category", filters.category);
  if (filters.since) query.set("since", filters.since);
  if (filters.until) query.set("until", filters.until);
  return query;
}

export async function getForensicTrace(
  token: string,
  queriedBy: string,
  filters: ForensicTraceFilters = {}
): Promise<ForensicTraceResult> {
  const query = forensicTraceQuery(queriedBy, filters);
  const response = await request(
    "reporting-service",
    `forensic-trace?${query.toString()}`,
    {},
    token
  );
  return response.json();
}

export async function exportForensicTrace(
  token: string,
  queriedBy: string,
  format: ReportFormat,
  filters: ForensicTraceFilters = {}
): Promise<Blob> {
  const query = forensicTraceQuery(queriedBy, filters);
  query.set("format", format);
  const response = await request(
    "reporting-service",
    `forensic-trace/export?${query.toString()}`,
    {},
    token
  );
  return response.blob();
}

// Audit-Tiefe fuer den Forensik-Trace (5.4b, seit P7-S2c) - Basis-
// Konfiguration + Rollen-Overrides in document-service, steuert ob
// document.viewed/document.downloaded ueberhaupt publiziert werden.
export interface AuditTraceConfig {
  log_viewed: boolean;
  log_downloaded: boolean;
  updated_at: string;
}

export interface AuditTraceRoleOverride {
  role: string;
  log_viewed: boolean | null;
  log_downloaded: boolean | null;
  updated_at: string;
}

export async function getAuditTraceConfig(token: string): Promise<AuditTraceConfig> {
  const response = await request("document-service", "audit-trace-config", {}, token);
  return response.json();
}

export async function updateAuditTraceConfig(
  token: string,
  logViewed: boolean,
  logDownloaded: boolean
): Promise<AuditTraceConfig> {
  const response = await request(
    "document-service",
    "audit-trace-config",
    {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ log_viewed: logViewed, log_downloaded: logDownloaded }),
    },
    token
  );
  return response.json();
}

export async function listAuditTraceRoleOverrides(
  token: string
): Promise<AuditTraceRoleOverride[]> {
  const response = await request("document-service", "audit-trace-role-overrides", {}, token);
  return response.json();
}

export async function putAuditTraceRoleOverride(
  token: string,
  role: string,
  logViewed: boolean | null,
  logDownloaded: boolean | null
): Promise<AuditTraceRoleOverride> {
  const response = await request(
    "document-service",
    `audit-trace-role-overrides/${encodeURIComponent(role)}`,
    {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ log_viewed: logViewed, log_downloaded: logDownloaded }),
    },
    token
  );
  return response.json();
}

export async function deleteAuditTraceRoleOverride(token: string, role: string): Promise<void> {
  await request(
    "document-service",
    `audit-trace-role-overrides/${encodeURIComponent(role)}`,
    { method: "DELETE" },
    token
  );
}
