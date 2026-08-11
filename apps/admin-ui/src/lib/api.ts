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
  // Aussonderung & Langzeitarchivierung (5.6, seit P7-S3) - gilt wie
  // default_retention_days für beide appliesTo-Werte. null = kein
  // Typ-Default (kein automatischer Aussonderungs-Zeitpunkt).
  default_archive_after_days: number | null;
  archive_encryption_enabled: boolean;
  // Verschlusssachen-Einstufung (2.5, seit P15-S1, mehrstufig seit P17-S2,
  // 14.2) - nur für applies_to="document" zulässig. null = nicht eingestuft.
  classification_level: ClassificationLevel | null;
}

// Die vier gängigen deutschen VS-Einstufungen (14.2, P17-S2) - wörtlich aus
// dem Konzepttext, siehe object-type-service.schemas.ClassificationLevel.
export type ClassificationLevel = "VS-NfD" | "VS-VERTRAULICH" | "GEHEIM" | "STRENG GEHEIM";

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
    defaultArchiveAfterDays: number | null;
    archiveEncryptionEnabled: boolean;
    classificationLevel: ClassificationLevel | null;
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
      default_archive_after_days: params.defaultArchiveAfterDays,
      archive_encryption_enabled: params.archiveEncryptionEnabled,
      classification_level: params.classificationLevel,
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
    defaultArchiveAfterDays: number | null;
    archiveEncryptionEnabled: boolean;
    classificationLevel: ClassificationLevel | null;
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
        default_archive_after_days: params.defaultArchiveAfterDays,
        archive_encryption_enabled: params.archiveEncryptionEnabled,
        classification_level: params.classificationLevel,
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
  payload: Record<string, unknown>;
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

// Aussonderung & Langzeitarchivierung (5.6, seit P7-S3) - reine
// Status-/Rückhol-Ansicht auf die vom archival-service geführte
// Transfer-Zustandsmaschine (pending -> locked -> copied -> verified ->
// released -> dehydrated, + failed).
export interface ArchivalTransfer {
  id: string;
  document_id: string;
  status: string;
  archive_format: string | null;
  encrypted: boolean;
  storage_object_key: string | null;
  checksum_sha256: string | null;
  error_message: string | null;
  locked_at: string | null;
  copied_at: string | null;
  verified_at: string | null;
  released_at: string | null;
  dehydrated_at: string | null;
  rehydrated_at: string | null;
  created_at: string;
  updated_at: string;
}

export async function listArchivalTransfers(
  token: string,
  status?: string
): Promise<ArchivalTransfer[]> {
  const query = status ? `?status=${encodeURIComponent(status)}` : "";
  const response = await request("archival-service", `archival-transfers${query}`, {}, token);
  return response.json();
}

// Rückholung (5.6) - erfordert `archive_retrieval_role` (Default
// "dms-admin") im X-DMS-Roles-Header, den das Gateway aus dem Access Token
// setzt, nicht diese Funktion selbst.
export async function retrieveArchivalTransfer(
  token: string,
  transferId: string
): Promise<ArchivalTransfer> {
  const response = await request(
    "archival-service",
    `archival-transfers/${transferId}/retrieve`,
    { method: "POST" },
    token
  );
  return response.json();
}

// XDOMEA-Aussonderung für Umlaufmappen (5.6, seit P7-S3b) - erzeugt eine
// Aussonderungsnachricht (XDOMEA 4.0.0) + packt die referenzierten
// Dokumentinhalte in ein ZIP, kein "dehydrated"-Status (Case besitzt keinen
// eigenen Live-Inhalt) und keine Rückschreib-Rückholung, nur ein Download.
export interface CaseArchivalTransfer {
  id: string;
  case_id: string;
  status: string;
  encrypted: boolean;
  storage_object_key: string | null;
  checksum_sha256: string | null;
  error_message: string | null;
  locked_at: string | null;
  packaged_at: string | null;
  verified_at: string | null;
  released_at: string | null;
  created_at: string;
  updated_at: string;
}

export async function listCaseArchivalTransfers(
  token: string,
  status?: string
): Promise<CaseArchivalTransfer[]> {
  const query = status ? `?status=${encodeURIComponent(status)}` : "";
  const response = await request(
    "archival-service",
    `case-archival-transfers${query}`,
    {},
    token
  );
  return response.json();
}

export async function downloadCaseArchivalPackage(
  token: string,
  transferId: string
): Promise<Blob> {
  const response = await request(
    "archival-service",
    `case-archival-transfers/${transferId}/package`,
    {},
    token
  );
  return response.blob();
}

export interface CaseArchivalConfig {
  default_archive_after_days_closed: number | null;
  archive_encryption_enabled: boolean;
  updated_at: string;
}

export async function getCaseArchivalConfig(token: string): Promise<CaseArchivalConfig> {
  const response = await request("case-service", "case-archival-config", {}, token);
  return response.json();
}

export async function updateCaseArchivalConfig(
  token: string,
  defaultArchiveAfterDaysClosed: number | null,
  archiveEncryptionEnabled: boolean
): Promise<CaseArchivalConfig> {
  const response = await request(
    "case-service",
    "case-archival-config",
    {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        default_archive_after_days_closed: defaultArchiveAfterDaysClosed,
        archive_encryption_enabled: archiveEncryptionEnabled,
      }),
    },
    token
  );
  return response.json();
}

// Query- & Trace-Konsole (6.1, seit P8-S1) - `X-DMS-Principal` wird vom
// Gateway aus dem Bearer-Token injiziert, nicht hier gesetzt (siehe
// `request()` oben). Nur die strukturierte Filter-API ist an die UI
// angebunden - der freie SQL-Pfad (`POST /query`) bleibt ohne installiertes
// Parser-Plugin (ADR 0031) ohnehin ungenutzt, siehe docs/services/admin-ui.md.
export interface QueryEvent {
  id: number;
  event_type: string;
  occurred_at: string;
  service_name: string;
  subject: string | null;
  actor: string | null;
  payload: Record<string, unknown>;
}

export interface QueryResult {
  events: QueryEvent[];
  total_before_filter: number;
  total_after_filter: number;
  superuser: boolean;
}

export interface QueryEventFilters {
  actor?: string;
  subject?: string;
  eventType?: string;
  since?: string;
  until?: string;
}

export async function listQueryEvents(
  token: string,
  filters: QueryEventFilters = {}
): Promise<QueryResult> {
  const query = new URLSearchParams();
  if (filters.actor) query.set("actor", filters.actor);
  if (filters.subject) query.set("subject", filters.subject);
  if (filters.eventType) query.set("event_type", filters.eventType);
  if (filters.since) query.set("since", filters.since);
  if (filters.until) query.set("until", filters.until);
  const response = await request("query-service", `query/events?${query.toString()}`, {}, token);
  return response.json();
}

// Manipulationsmodus (6.1, seit P8-S2/P8-S2b) - die drei bekannten
// Aktionstypen sind hartkodierter Spiegel von query_service/manipulation.py's
// Katalog (kein generisches Backend-Schema dafuer, siehe docs/services/
// query-service.md).
export const MANIPULATION_ACTION_TYPES = [
  "document.attribute_reset",
  "permission.role_assignment.delete",
  "object_type.update",
] as const;

export type ManipulationActionType = (typeof MANIPULATION_ACTION_TYPES)[number];

export interface ManipulationModeStatus {
  active: boolean;
  activated_by: string | null;
  expires_at: string | null;
}

export async function getManipulationModeStatus(token: string): Promise<ManipulationModeStatus> {
  const response = await request("query-service", "manipulation-mode/status", {}, token);
  return response.json();
}

export async function activateManipulationMode(
  token: string,
  durationMinutes: number
): Promise<ManipulationModeStatus> {
  const response = await request(
    "query-service",
    "manipulation-mode/activate",
    jsonInit({ duration_minutes: durationMinutes }),
    token
  );
  return response.json();
}

export async function deactivateManipulationMode(token: string): Promise<ManipulationModeStatus> {
  const response = await request(
    "query-service",
    "manipulation-mode/deactivate",
    { method: "POST" },
    token
  );
  return response.json();
}

export interface DryRunResult {
  action_type: string;
  preview: string;
  is_critical: boolean;
  dry_run_token: string;
}

export async function dryRunManipulation(
  token: string,
  actionType: ManipulationActionType,
  params: Record<string, unknown>
): Promise<DryRunResult> {
  const response = await request(
    "query-service",
    "manipulate/dry-run",
    jsonInit({ action_type: actionType, params }),
    token
  );
  return response.json();
}

export interface ManipulateExecuteResult {
  status: "executed" | "pending_approval";
  result: Record<string, unknown> | null;
  approval_request_id: string | null;
}

export async function executeManipulation(
  token: string,
  dryRunToken: string
): Promise<ManipulateExecuteResult> {
  const response = await request(
    "query-service",
    "manipulate/execute",
    jsonInit({ dry_run_token: dryRunToken }),
    token
  );
  return response.json();
}

export async function listPendingManipulationApprovals(token: string): Promise<ApprovalRequest[]> {
  const response = await request(
    "permission-service",
    "approval-requests?status=pending",
    {},
    token
  );
  const all: ApprovalRequest[] = await response.json();
  return all.filter((approvalRequest) =>
    (MANIPULATION_ACTION_TYPES as readonly string[]).includes(approvalRequest.action_type)
  );
}

// Lizenzsystem (Konzept 9, P9-S1/S2) - `license-service` selbst gategatet
// den Upload (`admin.license`), der Statusendpunkt bleibt ungegated (auch
// von registry-service/Admin-UI ohne Principal-Header abgefragt).
export interface LicenseDimensionUsage {
  limit: number | null;
  current: number | null;
  exceeded: boolean;
}

export interface LicenseStatus {
  installed: boolean;
  valid: boolean;
  invalid_reason: string | null;
  issued_at: string | null;
  expires_at: string | null;
  days_remaining: number | null;
  user_model: string | null;
  users: LicenseDimensionUsage | null;
  storage_gb: LicenseDimensionUsage | null;
  documents: LicenseDimensionUsage | null;
  licensed_components: string[] | null;
  limits_exceeded: string[];
}

export async function getLicenseStatus(token: string): Promise<LicenseStatus> {
  const response = await request("license-service", "license/status", {}, token);
  return response.json();
}

export async function uploadLicense(token: string, licenseToken: string): Promise<LicenseStatus> {
  const response = await request(
    "license-service",
    "license",
    jsonInit({ license_token: licenseToken }),
    token
  );
  return response.json();
}

// Öffentlicher Freigabelink (4.2a, P14-S10) - installationsweiter Schalter,
// gleiches Get/Update-Muster wie RetentionConfig/TrashConfig (document-service).
export interface ShareLinkConfig {
  enabled: boolean;
  max_validity_days: number;
  updated_at: string;
}

export async function getShareLinkConfig(token: string): Promise<ShareLinkConfig> {
  const response = await request("document-service", "share-link-config", {}, token);
  return response.json();
}

export async function updateShareLinkConfig(
  token: string,
  payload: { enabled: boolean; maxValidityDays: number }
): Promise<ShareLinkConfig> {
  const response = await request(
    "document-service",
    "share-link-config",
    {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        enabled: payload.enabled,
        max_validity_days: payload.maxValidityDays,
      }),
    },
    token
  );
  return response.json();
}

// Stellvertretung bei Abwesenheit (4.4a, P14-S11) - reine Admin-Übersicht +
// Widerrufsmöglichkeit (Konzept-Wortlaut: "kann jederzeit vorzeitig von der
// vertretenen Person oder einer berechtigten Admin-Rolle beendet werden").
// Anlegen selbst ist bewusst kein Admin-UI-Feature (Selbstverwaltung, siehe
// user-ui's `DelegationsPane`).
export interface Delegation {
  id: string;
  delegator_principal_id: string;
  deputy_principal_id: string;
  starts_at: string;
  ends_at: string;
  scope_object_type_ids: number[] | null;
  scope_process_definition_ids: number[] | null;
  scope_folder_resource_ids: string[] | null;
  created_at: string;
  revoked_at: string | null;
  revoked_by: string | null;
}

export async function listAllDelegations(token: string): Promise<Delegation[]> {
  const response = await request("permission-service", "delegations", {}, token);
  return response.json();
}

export async function revokeDelegationAsAdmin(token: string, delegationId: string): Promise<void> {
  await request(
    "permission-service",
    `delegations/${encodeURIComponent(delegationId)}`,
    { method: "DELETE" },
    token
  );
}

// Vorkonfigurierte Konfigurationspakete (14.1, P17-S1) - "Paket" ist am Ende
// nur ein `ConfigDocument` (7.3-Format, bereits seit P12-S3 bestehend) mit
// einem optionalen, rein beschreibenden `manifest`. Der Dokumentinhalt selbst
// bleibt hier bewusst lose typisiert (`Record<string, unknown>` je Kategorie)
// statt jede der neun Kategorien 1:1 nachzubilden - diese Seite liest ein vom
// Nutzer hochgeladenes JSON-Dokument ein und reicht es unverändert an
// config-service weiter (Vorschau/Anwendung), sie interpretiert dessen
// Feldinhalte selbst nicht.
export const CONFIG_CATEGORIES = [
  "object_types",
  "workflows",
  "dmn_definitions",
  "business_calendars",
  "roles",
  "approval_config",
  "sensor_config",
  "federation_config",
  "realm_roles",
] as const;
export type ConfigCategory = (typeof CONFIG_CATEGORIES)[number];

export interface PackageManifest {
  name: string;
  version: string;
  compatibility_range: string;
  description?: string;
  origin?: string;
  license?: string;
}

export type ConfigDocument = {
  schema_version: string;
  exported_at: string;
  manifest?: PackageManifest | null;
} & Partial<Record<ConfigCategory, unknown>>;

function categoryQuery(categories?: ConfigCategory[]): string {
  if (!categories || categories.length === 0) return "";
  const query = new URLSearchParams();
  categories.forEach((c) => query.append("categories", c));
  return `?${query.toString()}`;
}

export async function exportConfig(
  token: string,
  categories?: ConfigCategory[]
): Promise<ConfigDocument> {
  const response = await request(
    "config-service",
    `config/export${categoryQuery(categories)}`,
    {},
    token
  );
  return response.json();
}

export interface CategoryDelta {
  only_in_base: string[];
  only_in_compare: string[];
  differing: Record<string, Record<string, { base: unknown; compare: unknown }>>;
  identical: string[];
}

export interface CompareResult {
  schema_version: string;
  base_exported_at: string;
  compare_exported_at: string;
  categories: Record<string, CategoryDelta>;
}

// `base` weglassen zieht bei config-service automatisch den eigenen aktuellen
// Live-Export heran (7.5-Anwendungsfall "was würde sich ändern, wenn ich
// dieses Paket importiere") - das ist die von dieser Seite genutzte Vorschau
// vor dem eigentlichen Anwenden.
export async function compareConfig(
  token: string,
  compareDoc: ConfigDocument,
  categories?: ConfigCategory[]
): Promise<CompareResult> {
  const response = await request(
    "config-service",
    "config/compare",
    jsonInit({ compare: compareDoc, categories: categories && categories.length ? categories : undefined }),
    token
  );
  return response.json();
}

export interface ConfigCategoryResult {
  created: number;
  updated: number;
  skipped: number;
  errors: string[];
}

export interface ConfigImportResult {
  schema_version: string;
  results: Record<string, ConfigCategoryResult>;
}

export async function importConfig(
  token: string,
  document: ConfigDocument,
  categories?: ConfigCategory[]
): Promise<ConfigImportResult> {
  const response = await request(
    "config-service",
    `config/import${categoryQuery(categories)}`,
    jsonInit(document),
    token
  );
  return response.json();
}
