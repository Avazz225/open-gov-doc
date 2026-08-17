import { GATEWAY_BASE_URL as DEFAULT_GATEWAY_BASE_URL } from "./config";

// Mutable instead of a fixed import (P4-S5, multi-installation, Concept 8):
// the admin UI can manage multiple installations, each with its own gateway
// endpoint - `InstallationProvider` calls `setGatewayBaseUrl()` on every
// installation switch. All existing callers of this module remain
// unchanged (they don't know the URL, only the `service_type`/path).
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

// Every call goes through the gateway (3.5): /api/{service_type}/{path}
// instead of direct backend addresses - registry resolution and auth
// checks happen there, not here.
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

// Domain-separated admin roles (4.6, P6-S5): native to the Permission
// Service, NOT a Keycloak realm role (unlike `realm_roles` above) - the
// same source also used by backend gating (e.g. Auth Service `/users`),
// see ADR 0023.
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

// Since P17-S3 (14.2 "permission change"): `POST /role-assignments` can
// optionally be gated by the four-eyes principle - `role_assignment` is
// only set when `status === "created"`.
export interface RoleAssignmentActionResult {
  status: "created" | "pending_approval";
  role_assignment: RoleAssignment | null;
  approval_request_id: string | null;
}

export async function listRoleAssignments(token: string): Promise<RoleAssignment[]> {
  const response = await request("permission-service", "role-assignments", {}, token);
  return response.json();
}

export async function createRoleAssignment(
  token: string,
  params: { principalType: string; principalId: string; roleId: number; resourceId: string }
): Promise<RoleAssignmentActionResult> {
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

// Admin-creatable groups (Post-Roadmap Phase 22 Session 2) - complement the
// hardcoded "everyone" group that has existed since Phase 19 Session 2.
// Same `admin.user_management` self-gating as `POST`/`PUT /roles` (the
// gateway already injects `X-DMS-Principal` from the access token, no
// manual header setting needed here - same pattern as `createRole`).
export interface Group {
  id: string;
  name: string;
  description: string;
  created_at: string;
}

export interface GroupMember {
  id: number;
  group_id: string;
  principal_id: string;
}

export async function listGroups(token: string): Promise<Group[]> {
  const response = await request("permission-service", "groups", {}, token);
  return response.json();
}

export async function createGroup(
  token: string,
  params: { name: string; description: string }
): Promise<Group> {
  const response = await request("permission-service", "groups", jsonInit(params), token);
  return response.json();
}

export async function deleteGroup(token: string, groupId: string): Promise<void> {
  await request(
    "permission-service",
    `groups/${encodeURIComponent(groupId)}`,
    { method: "DELETE" },
    token
  );
}

export async function listGroupMembers(token: string, groupId: string): Promise<GroupMember[]> {
  const response = await request(
    "permission-service",
    `groups/${encodeURIComponent(groupId)}/members`,
    {},
    token
  );
  return response.json();
}

export async function addGroupMember(
  token: string,
  groupId: string,
  principalId: string
): Promise<GroupMember> {
  const response = await request(
    "permission-service",
    `groups/${encodeURIComponent(groupId)}/members`,
    jsonInit({ principal_id: principalId }),
    token
  );
  return response.json();
}

export async function removeGroupMember(
  token: string,
  groupId: string,
  principalId: string
): Promise<void> {
  await request(
    "permission-service",
    `groups/${encodeURIComponent(groupId)}/members/${encodeURIComponent(principalId)}`,
    { method: "DELETE" },
    token
  );
}

// Generic four-eyes principle settings page (Post-Roadmap Phase 22 Session
// 3) - `GET /approval-config` returns ONLY already-configured action types
// (if a row is missing, `requires_approval=false` applies implicitly, see
// `docs/services/permission-service.md`) - there is no fixed, hardcoded
// catalog of all action types existing in the system, hence the form below
// for adding a new, not-yet-configured action type.
export interface ApprovalActionConfig {
  action_type: string;
  requires_approval: boolean;
  required_permission: string | null;
  updated_at: string;
}

export async function listApprovalConfig(token: string): Promise<ApprovalActionConfig[]> {
  const response = await request("permission-service", "approval-config", {}, token);
  return response.json();
}

// IMPORTANT: `required_permission` must always be sent explicitly (even if
// only `requires_approval` is being changed) - otherwise the backend
// overwrites it with `null`, see
// `permission_service.repository.set_approval_config`.
export async function putApprovalConfig(
  token: string,
  actionType: string,
  params: { requiresApproval: boolean; requiredPermission: string | null }
): Promise<ApprovalActionConfig> {
  const response = await request(
    "permission-service",
    `approval-config/${encodeURIComponent(actionType)}`,
    {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        requires_approval: params.requiresApproval,
        required_permission: params.requiredPermission,
      }),
    },
    token
  );
  return response.json();
}

// Available attribute types of the Constraint Engine (4.5) - see
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

// Sentinel for "can be placed directly under the root" (2.2a, ADR 0013) -
// must match the backend constant `ROOT_PARENT_TYPE` exactly.
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
  // Reference number generator (2.2, since P5e-S1/S3) - both are only set
  // for applies_to="document". kennzeichen_display_override is a tri-state:
  // null = the global default (KennzeichenConfig) applies.
  kennzeichen_format: string | null;
  kennzeichen_display_override: boolean | null;
  // Minimum signature level (3.10, since P6-S7) - only set for
  // applies_to="document", null = no requirement. Enforced by the Signature
  // Service on every signing operation, only configured here.
  required_signature_level: "ses" | "aes" | "qes" | null;
  // Retention (5.2, since P7-S1) - unlike reference number/signature, this
  // applies equally to applies_to="document" AND "folder". deletion_reason_
  // required_override is a tri-state like kennzeichen_display_override:
  // null = the installation-wide default (RetentionConfig) applies.
  default_retention_days: number | null;
  deletion_reason_required_override: boolean | null;
  // Archival & long-term retention (5.6, since P7-S3) - applies to both
  // appliesTo values like default_retention_days. null = no type default
  // (no automatic archival due date).
  default_archive_after_days: number | null;
  archive_encryption_enabled: boolean;
  // Classified information level (2.5, since P15-S1, multi-level since
  // P17-S2, 14.2) - only permitted for applies_to="document". null = not
  // classified.
  classification_level: ClassificationLevel | null;
}

// The four common German classified-information levels (14.2, P17-S2) -
// taken verbatim from the concept text, see
// object-type-service.schemas.ClassificationLevel.
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

// Deliberately no `name`/`appliesTo` in the payload - both are immutable
// server-side after creation (see object-type-service). `namingConstraints`/
// `conditions` are passed through unchanged rather than being editable in
// the guided UI (out of scope for P5b-S3) - without this, saving via this
// editor would silently reset them to their default.
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

// Form layouts (2.2b, since P5b-S2, ADR 0014) - `is_custom: false` means
// "generated smart layout, not saved", `true` means "explicit override
// saved via PUT".
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

// ocrEnabled itself is deliberately not a field here - it is a Docker
// Compose profile opt-out (ADR 0016, P5b-S5), not a runtime setting of the
// service. If ocr-service isn't deployed, this call fails with a connection
// error - `OcrSettings.tsx` then displays that as "unreachable" instead of
// a value.
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

// Analogous to getOcrConfig/updateOcrConfig - Document Service, not OCR
// Service (P5d-S1, format whitelist instead of OCR filter list).
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

// Retention/legal hold/forced deletion (5.2/5.2a, since P7-S1) - same
// get/update pattern as UploadConfig above, also document-service.
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

// Retention/legal hold/forced deletion for folders (5.2/5.2a, since
// P7-S1b) - separate, independently configurable configs (not the same
// rows as document-service, see docs/services/folder-service.md).
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

// Global display default of the reference number generator (2.2, since
// P5e-S3) - lives in the Object Type Service, not the Document Service,
// since the per-object-type override
// (`ObjectType.kennzeichen_display_override`) already lives there too.
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
  // Retention/WORM (5.1/5.2a, since P7-S1). Live-editable via
  // `updateTargetConfig()` since Post-Roadmap Phase 22 Session 7 (ADR 0092).
  object_lock_mode: "governance" | null;
  role: "archive" | null;
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

// Target metadata (Post-Roadmap Phase 22 Session 7, ADR 0092) - ONLY
// `object_lock_mode`/`role` per already-configured target, takes effect
// without restarting the Storage Service. The target set itself
// (credentials/structure) remains env-var-only, see ADR 0091/0092
// "Rationale".
export async function updateTargetConfig(
  token: string,
  targetId: string,
  params: { objectLockMode: "governance" | null; role: "archive" | null }
): Promise<GuardStatusEntry> {
  const response = await request(
    "storage-service",
    `guard-status/${encodeURIComponent(targetId)}/config`,
    {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ object_lock_mode: params.objectLockMode, role: params.role }),
    },
    token
  );
  return response.json();
}

// Operational parameters (Post-Roadmap Phase 22 Session 6, ADR 0091) -
// unlike the target set itself (credentials, deliberately remains
// env-var-only), these contain no secrets, so they're live-editable, taking
// effect without restarting the Storage Service.
export interface OperationalConfig {
  write_strategy: "quorum" | "primary_async";
  quorum_count: number;
  max_replication_attempts: number;
  updated_at: string;
}

export async function getOperationalConfig(token: string): Promise<OperationalConfig> {
  const response = await request("storage-service", "operational-config", {}, token);
  return response.json();
}

export async function updateOperationalConfig(
  token: string,
  params: { writeStrategy: "quorum" | "primary_async"; quorumCount: number; maxReplicationAttempts: number }
): Promise<OperationalConfig> {
  const response = await request(
    "storage-service",
    "operational-config",
    {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        write_strategy: params.writeStrategy,
        quorum_count: params.quorumCount,
        max_replication_attempts: params.maxReplicationAttempts,
      }),
    },
    token
  );
  return response.json();
}

// Signature connector levels (Post-Roadmap Phase 22 Session 6, ADR 0091) -
// `id`/`type` are structurally fixed (`Settings.signature_providers`), only
// `levels` is admin-editable, taking effect without restarting the
// Signature Service.
export interface SignatureProviderStatus {
  id: string;
  type: "internal" | "qtsp";
  levels: ("ses" | "aes" | "qes")[];
}

export async function getSignatureConfig(token: string): Promise<SignatureProviderStatus[]> {
  const response = await request("signature-service", "signature-config", {}, token);
  return response.json();
}

export async function updateSignatureConfig(
  token: string,
  entries: { id: string; levels: string[] }[]
): Promise<SignatureProviderStatus[]> {
  const response = await request(
    "signature-service",
    "signature-config",
    {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(entries),
    },
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

// Superuser break-glass (4.6, P6-S5) - activation itself runs through the
// already-existing generic four-eyes principle mechanism of the Permission
// Service (P6-S4, ADR 0022), no separate approval system here.
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

// Emergency shutdown (4.8, P6-S6) - triggering uses the same generic
// four-eyes principle mechanism as break-glass, but here with a direct
// execution path (see permission-service `POST /maintenance-mode/trigger`)
// if no four-eyes principle intermediate step is configured.
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

// Standard reports (5.4a, since P7-S2b) - four report types, each with a
// JSON view + an export endpoint (CSV/PDF), plus schedulable delivery.
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

// Forensic trace (5.4b, since P7-S2c) - object-related tracking ("all
// actions by user X"/"all users on document Y") built on the P7-S2 audit
// filter API, second function of the reporting-service (5.4).
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

// Audit depth for the forensic trace (5.4b, since P7-S2c) - base
// configuration + role overrides in document-service, controls whether
// document.viewed/document.downloaded are published at all.
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

// Archival & long-term retention (5.6, since P7-S3) - a pure
// status/retrieval view onto the transfer state machine maintained by
// archival-service (pending -> locked -> copied -> verified -> released ->
// dehydrated, + failed -> failed_permanent since Post-Roadmap Phase 20
// Session 2/7, ADR 0078).
export interface ArchivalTransfer {
  id: string;
  document_id: string;
  status: string;
  archive_format: string | null;
  encrypted: boolean;
  storage_object_key: string | null;
  checksum_sha256: string | null;
  error_message: string | null;
  attempts: number;
  next_retry_at: string | null;
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

// Manual archival trigger (5.6, since Post-Roadmap Phase 22 Session 1) -
// sets `archive_after` to now on `document-service`, making the document
// due immediately instead of only after the object type deadline expires.
// This itself does NOT yet create an `ArchivalTransfer` row - that only
// happens on `archival-service`'s next poll tick (default hourly, see
// `docs/services/archival-service.md`), hence no immediate reload of the
// transfer table after the call.
export async function requestDocumentArchive(token: string, documentId: string): Promise<void> {
  await request(
    "document-service",
    `documents/${encodeURIComponent(documentId)}/archive-request`,
    { method: "POST" },
    token
  );
}

// Retrieval (5.6) - requires `archive_retrieval_role` (default "dms-admin")
// in the X-DMS-Roles header, which the gateway sets from the access token,
// not this function itself.
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

// Manual restart of a permanently failed transfer (Post-Roadmap Phase 20
// Session 2/7, ADR 0078) - only meaningful for `failed_permanent` (`409`
// otherwise).
export async function retryArchivalTransfer(
  token: string,
  transferId: string
): Promise<ArchivalTransfer> {
  const response = await request(
    "archival-service",
    `archival-transfers/${transferId}/retry`,
    { method: "POST" },
    token
  );
  return response.json();
}

// XDOMEA archival for case files (5.6, since P7-S3b) - generates an
// archival message (XDOMEA 4.0.0) + packs the referenced document contents
// into a ZIP, no "dehydrated" status (the case holds no live content of
// its own) and no write-back retrieval, download only.
export interface CaseArchivalTransfer {
  id: string;
  case_id: string;
  status: string;
  encrypted: boolean;
  storage_object_key: string | null;
  checksum_sha256: string | null;
  error_message: string | null;
  attempts: number;
  next_retry_at: string | null;
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

export async function retryCaseArchivalTransfer(
  token: string,
  transferId: string
): Promise<CaseArchivalTransfer> {
  const response = await request(
    "archival-service",
    `case-archival-transfers/${transferId}/retry`,
    { method: "POST" },
    token
  );
  return response.json();
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

// Processing failure visibility (Post-Roadmap Phase 20 Session 7) - a pure
// status/restart view onto `failed_permanent` records in three independent
// services (notification-/rendering-/ocr-service), analogous to
// `ArchivalTransfersView` above, but here as one shared new page instead of
// a page per service (same "small section instead of its own page"
// principle, applied here at the page level).
export interface Notification {
  id: string;
  channel: string;
  recipient: string;
  subject: string;
  body: string;
  status: string;
  error: string | null;
  attempts: number;
  next_retry_at: string | null;
  created_at: string;
  sent_at: string | null;
}

export async function listNotifications(
  token: string,
  status?: string
): Promise<Notification[]> {
  const query = status ? `?status=${encodeURIComponent(status)}` : "";
  const response = await request("notification-service", `notifications${query}`, {}, token);
  return response.json();
}

// Unlike the other three resilience services of this phase,
// notification-service has NO permission-service integration (ADR
// 0079/0081) - the retry endpoint is therefore without an RBAC gate, this
// function needs no additional capability handling.
export async function retryNotification(token: string, id: string): Promise<Notification> {
  const response = await request(
    "notification-service",
    `notifications/${id}/retry`,
    { method: "POST" },
    token
  );
  return response.json();
}

// Configurable email templates (post-roadmap phase 30, ADR 0111) - same
// "fixed catalog, no row = fallback to hardcoded default" shape as
// `ApprovalActionConfig` above, except the set of `use_case`s IS a fixed,
// known catalog here (`listEmailTemplateUseCases`), since `consumer.py`'s
// handlers are a closed set of branches, not an open-ended list of callers.
export interface EmailTemplate {
  id: number;
  use_case: string;
  recipient_domain_pattern: string | null;
  subject_template: string;
  body_template: string;
  updated_at: string;
}

export interface EmailTemplateUseCase {
  use_case: string;
  description: string;
  placeholders: string[];
}

export async function listEmailTemplateUseCases(token: string): Promise<EmailTemplateUseCase[]> {
  const response = await request("notification-service", "email-template-use-cases", {}, token);
  return response.json();
}

export async function listEmailTemplates(
  token: string,
  useCase?: string
): Promise<EmailTemplate[]> {
  const query = useCase ? `?use_case=${encodeURIComponent(useCase)}` : "";
  const response = await request("notification-service", `email-templates${query}`, {}, token);
  return response.json();
}

export async function putEmailTemplate(
  token: string,
  useCase: string,
  params: { recipientDomain: string | null; subjectTemplate: string; bodyTemplate: string }
): Promise<EmailTemplate> {
  const path = params.recipientDomain
    ? `email-templates/${encodeURIComponent(useCase)}/by-domain/${encodeURIComponent(params.recipientDomain)}`
    : `email-templates/${encodeURIComponent(useCase)}`;
  const response = await request(
    "notification-service",
    path,
    {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        subject_template: params.subjectTemplate,
        body_template: params.bodyTemplate,
      }),
    },
    token
  );
  return response.json();
}

export async function deleteEmailTemplate(token: string, id: number): Promise<void> {
  await request("notification-service", `email-templates/${id}`, { method: "DELETE" }, token);
}

export interface Rendition {
  id: string;
  document_id: string;
  version_number: number;
  rendition_type: string;
  status: string;
  error_message: string | null;
  attempts: number;
  next_retry_at: string | null;
  created_at: string;
  updated_at: string;
}

export async function listRenditions(token: string, status?: string): Promise<Rendition[]> {
  const query = status ? `?status=${encodeURIComponent(status)}` : "";
  const response = await request("rendering-service", `renditions${query}`, {}, token);
  return response.json();
}

export async function retryRendition(token: string, id: string): Promise<Rendition> {
  const response = await request(
    "rendering-service",
    `renditions/${id}/retry`,
    { method: "POST" },
    token
  );
  return response.json();
}

export interface OcrResult {
  id: string;
  document_id: string;
  version_number: number;
  status: string;
  error_message: string | null;
  attempts: number;
  next_retry_at: string | null;
  created_at: string;
  updated_at: string;
}

export async function listOcrResults(token: string, status?: string): Promise<OcrResult[]> {
  const query = status ? `?status=${encodeURIComponent(status)}` : "";
  const response = await request("ocr-service", `ocr-results${query}`, {}, token);
  return response.json();
}

export async function retryOcrResult(token: string, id: string): Promise<OcrResult> {
  const response = await request(
    "ocr-service",
    `ocr-results/${id}/retry`,
    { method: "POST" },
    token
  );
  return response.json();
}

// Query & trace console (6.1, since P8-S1) - `X-DMS-Principal` is injected
// by the gateway from the bearer token, not set here (see `request()`
// above). Only the structured filter API is wired to the UI - the free-form
// SQL path (`POST /query`) remains unused anyway without an installed
// parser plugin (ADR 0031), see docs/services/admin-ui.md.
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

// Manipulation mode (6.1, since P8-S2/P8-S2b) - the three known action
// types are a hardcoded mirror of query_service/manipulation.py's catalog
// (no generic backend schema for this, see
// docs/services/query-service.md).
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

// License system (Concept 9, P9-S1/S2) - `license-service` itself gates the
// upload (`admin.license`), the status endpoint remains ungated (also
// queried by registry-service/admin UI without a principal header).
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

// Public share link (4.2a, P14-S10) - installation-wide toggle, same
// get/update pattern as RetentionConfig/TrashConfig (document-service).
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

// Absence delegation (4.4a, P14-S11) - a pure admin overview + revocation
// capability (concept wording: "can be ended early at any time by the
// delegating person or an authorized admin role"). Creation itself is
// deliberately not an admin UI feature (self-service, see user-ui's
// `DelegationsPane`).
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

// Preconfigured configuration packages (14.1, P17-S1) - a "package" is
// ultimately just a `ConfigDocument` (7.3 format, already existing since
// P12-S3) with an optional, purely descriptive `manifest`. The document
// content itself remains deliberately loosely typed here
// (`Record<string, unknown>` per category) rather than modeling each of the
// nine categories 1:1 - this page reads in a JSON document uploaded by the
// user and passes it through unchanged to config-service (preview/apply),
// it does not interpret its field contents itself.
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

// Omitting `base` makes config-service automatically pull its own current
// live export (7.5 use case "what would change if I import this package") -
// this is the preview used by this page before actually applying it.
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

// Since P17-S3 (14.2 "configuration import"): `POST /config/import` can
// optionally be gated by the four-eyes principle - `result` is only set
// when `status === "applied"`.
export interface ConfigImportActionResult {
  status: "applied" | "pending_approval";
  result: ConfigImportResult | null;
  approval_request_id: string | null;
}

export async function importConfig(
  token: string,
  document: ConfigDocument,
  categories?: ConfigCategory[]
): Promise<ConfigImportActionResult> {
  const response = await request(
    "config-service",
    `config/import${categoryQuery(categories)}`,
    jsonInit(document),
    token
  );
  return response.json();
}

// Teamspaces admin overview (Post-Roadmap Phase 22 Session 5) - the first
// admin UI integration of `teamspace-service` at all. `GET /admin/teamspaces`
// is gated (`admin.teamspace_management`, new domain "domain-admin-
// teamspaces") - unlike `GET /teamspaces` (filtered by membership there,
// installation-wide here for all).
export interface TeamspaceAdmin {
  id: string;
  name: string;
  description: string;
  root_folder_id: string;
  created_by: string;
  created_at: string;
  updated_at: string;
  member_count: number;
}

export async function listAllTeamspaces(token: string): Promise<TeamspaceAdmin[]> {
  const response = await request("teamspace-service", "admin/teamspaces", {}, token);
  return response.json();
}
