import { GATEWAY_BASE_URL, WEBDAV_CONNECTOR_BASE_URL } from "./config";

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
// instead of direct backend addresses - registry resolution and auth checks
// happen there, not here.
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

// SSO/automatic login (post-roadmap feature)

export interface SsoConfig {
  enabled: boolean;
}

export async function getSsoConfig(): Promise<SsoConfig> {
  const response = await request("auth-service", "sso-config");
  return response.json();
}

export async function oidcAuthorize(redirectUri: string, state: string): Promise<string> {
  const response = await request(
    "auth-service",
    `oidc/authorize?redirect_uri=${encodeURIComponent(redirectUri)}&state=${encodeURIComponent(state)}`
  );
  const body = (await response.json()) as { authorization_url: string };
  return body.authorization_url;
}

export async function oidcCallback(code: string, redirectUri: string): Promise<TokenResponse> {
  const response = await request("auth-service", "oidc/callback", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ code, redirect_uri: redirectUri }),
  });
  return response.json();
}

export async function logoutSession(refresh_token: string): Promise<void> {
  await request("auth-service", "logout", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ refresh_token }),
  });
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

export interface Folder {
  id: string;
  name: string;
  parent_id: string | null;
  object_type_id: number | null;
  attributes: Record<string, unknown>;
  deleted_at: string | null;
  // Personal trash (2.5, P15-S1).
  deleted_by: string | null;
  // Retention/forced deletion for folders (5.2/5.2a, since P7-S1b).
  retention_until: string | null;
  full_deletion: boolean;
  pending_deletion_reason: string | null;
}

export async function listChildFolders(token: string, folderId: string): Promise<Folder[]> {
  const response = await request(
    "folder-service",
    `folders/${encodeURIComponent(folderId)}/children`,
    {},
    token
  );
  return response.json();
}

export async function createFolder(
  token: string,
  params: { name: string; parentId: string; createdBy: string; objectTypeId?: number }
): Promise<Folder> {
  const response = await request(
    "folder-service",
    "folders",
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        name: params.name,
        parent_id: params.parentId,
        created_by: params.createdBy,
        object_type_id: params.objectTypeId ?? null,
      }),
    },
    token
  );
  return response.json();
}

export async function renameFolder(token: string, folderId: string, name: string): Promise<Folder> {
  const response = await request(
    "folder-service",
    `folders/${encodeURIComponent(folderId)}`,
    {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name }),
    },
    token
  );
  return response.json();
}

// Moving folders via drag & drop (8, P23-S4) - separate function instead of
// reusing `renameFolder`, since this is semantically a different operation
// (parent folder instead of name) - the endpoint itself already existed
// unchanged before (`FolderUpdate.parent_id`), just without a frontend
// integration.
export async function moveFolder(
  token: string,
  folderId: string,
  newParentId: string
): Promise<Folder> {
  const response = await request(
    "folder-service",
    `folders/${encodeURIComponent(folderId)}`,
    {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ parent_id: newParentId }),
    },
    token
  );
  return response.json();
}

// Bulk metadata editing (8, P14-S12) - folder counterpart to
// `updateDocumentMetadata`; previously there was no dedicated function for
// this (only `renameFolder`, which sets exclusively `name`). Same
// full-replace semantics as the document counterpart - `attributes` replaces
// the entire existing value, no server-side merge.
export async function updateFolderAttributes(
  token: string,
  folderId: string,
  attributes: Record<string, unknown>
): Promise<Folder> {
  const response = await request(
    "folder-service",
    `folders/${encodeURIComponent(folderId)}`,
    {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ attributes }),
    },
    token
  );
  return response.json();
}

export async function deleteFolder(token: string, folderId: string): Promise<void> {
  await request(
    "folder-service",
    `folders/${encodeURIComponent(folderId)}`,
    { method: "DELETE" },
    token
  );
}

// Deletion request workflow for regular users (5.2, since P7-S1c) - two
// possible outcomes, same wrapper pattern for folders and documents:
// executed immediately, or deferred via the four-eyes principle (action type
// `folder.delete`/`document.delete`, independent of the already-existing
// retention-triggered forced deletion).
export interface FolderTrashResult {
  status: "trashed" | "pending_approval";
  folder: Folder | null;
  approval_request_id: string | null;
}

export async function trashFolder(
  token: string,
  folderId: string,
  deletedBy: string
): Promise<FolderTrashResult> {
  const response = await request(
    "folder-service",
    `folders/${encodeURIComponent(folderId)}/trash`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ deleted_by: deletedBy }),
    },
    token
  );
  return response.json();
}

export interface DocumentTrashResult {
  status: "trashed" | "pending_approval";
  document: DocumentSummary | null;
  approval_request_id: string | null;
}

export async function trashDocument(
  token: string,
  documentId: string,
  deletedBy: string
): Promise<DocumentTrashResult> {
  const response = await request(
    "document-service",
    `documents/${encodeURIComponent(documentId)}/trash`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ deleted_by: deletedBy }),
    },
    token
  );
  return response.json();
}

export async function restoreFolder(token: string, folderId: string): Promise<Folder> {
  const response = await request(
    "folder-service",
    `folders/${encodeURIComponent(folderId)}/restore`,
    { method: "POST" },
    token
  );
  return response.json();
}

export async function listDeletedFolders(token: string, parentId: string): Promise<Folder[]> {
  const response = await request(
    "folder-service",
    `folders/deleted?parent_id=${encodeURIComponent(parentId)}`,
    {},
    token
  );
  return response.json();
}

// Trash family (2.5, P15-S1) - installation-wide views instead of the
// folder-scoped listing above. "personal" needs no role (only the user's own
// deletion markers), "admin" requires the deletion-administration role
// server-side (403 otherwise).
export type TrashScope = "personal" | "admin";

export async function listDeletedFoldersGlobal(
  token: string,
  scope: TrashScope
): Promise<Folder[]> {
  const response = await request(
    "folder-service",
    `folders/deleted?scope=${encodeURIComponent(scope)}`,
    {},
    token
  );
  return response.json();
}

export async function purgeFolder(token: string, folderId: string): Promise<void> {
  await request(
    "folder-service",
    `folders/${encodeURIComponent(folderId)}/purge`,
    { method: "POST" },
    token
  );
}

export async function putFolderRetention(
  token: string,
  folderId: string,
  params: {
    retentionUntil: string | null;
    fullDeletion: boolean;
    reason?: string | null;
    notifyEmail?: string | null;
  }
): Promise<Folder> {
  const response = await request(
    "folder-service",
    `folders/${encodeURIComponent(folderId)}/retention`,
    {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        retention_until: params.retentionUntil,
        full_deletion: params.fullDeletion,
        reason: params.reason ?? null,
        notify_email: params.notifyEmail ?? null,
      }),
    },
    token
  );
  return response.json();
}

export interface FolderLegalHold {
  id: string;
  folder_id: string;
  reason: string | null;
  set_by: string;
  set_at: string;
  released_by: string | null;
  released_at: string | null;
}

export async function listFolderLegalHolds(
  token: string,
  folderId: string,
  activeOnly = false
): Promise<FolderLegalHold[]> {
  const response = await request(
    "folder-service",
    `legal-holds?folder_id=${encodeURIComponent(folderId)}&active_only=${activeOnly}`,
    {},
    token
  );
  return response.json();
}

export async function createFolderLegalHold(
  token: string,
  params: { folderId: string; setBy: string; reason?: string | null }
): Promise<FolderLegalHold> {
  const response = await request(
    "folder-service",
    "legal-holds",
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        folder_id: params.folderId,
        set_by: params.setBy,
        reason: params.reason ?? null,
      }),
    },
    token
  );
  return response.json();
}

export async function releaseFolderLegalHold(
  token: string,
  holdId: string,
  releasedBy: string
): Promise<FolderLegalHold> {
  const response = await request(
    "folder-service",
    `legal-holds/${encodeURIComponent(holdId)}/release`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ released_by: releasedBy }),
    },
    token
  );
  return response.json();
}

export interface ObjectTypeAttribute {
  name: string;
  type?: string;
  required?: boolean;
  min?: number;
  max?: number;
  pattern?: string;
}

export interface ObjectType {
  id: number;
  name: string;
  applies_to: string;
  attributes: ObjectTypeAttribute[];
  icon: string | null;
  // Reference-number generator (2.2, since P5e-S1/S3) - only set for
  // applies_to="document". kennzeichen_display_override is a tri-state:
  // null/undefined = the global default (KennzeichenConfig) applies, see
  // lib/kennzeichen.ts.
  kennzeichen_format?: string | null;
  kennzeichen_display_override?: boolean | null;
  // Classified-document classification level (2.5, P15-S1, multi-level since
  // P17-S2, 14.2) - only for applies_to="document".
  classification_level?: string | null;
}

export interface KennzeichenConfig {
  show_before_filename: boolean;
  updated_at: string;
}

export async function getKennzeichenConfig(token: string): Promise<KennzeichenConfig> {
  const response = await request("object-type-service", "kennzeichen-config", {}, token);
  return response.json();
}

export async function getObjectType(token: string, objectTypeId: number): Promise<ObjectType> {
  const response = await request(
    "object-type-service",
    `object-types/${objectTypeId}`,
    {},
    token
  );
  return response.json();
}

export async function listObjectTypes(
  token: string,
  appliesTo?: "document" | "folder"
): Promise<ObjectType[]> {
  const query = appliesTo ? `?applies_to=${appliesTo}` : "";
  const response = await request("object-type-service", `object-types${query}`, {}, token);
  return response.json();
}

// Form layouts (2.2b, since P5b-S2/ADR 0014) - since P5b-S4 they control the
// arrangement of attribute fields in the metadata panel, search form, and
// upload dialog. `is_custom: false` means "generated smart layout, not
// saved", `true` means "override saved via the admin-UI layout designer" -
// not meaningful for plain display here, included only for completeness.
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

export interface DocumentSummary {
  id: string;
  title: string;
  folder_id: string | null;
  object_type_id: number | null;
  attributes: Record<string, unknown>;
  current_version_number: number;
  deleted_at: string | null;
  // Personal trash (2.5, P15-S1).
  deleted_by: string | null;
  created_by: string;
  created_at: string;
  updated_at: string;
  // Retention/forced deletion (5.2/5.2a, since P7-S1).
  retention_until: string | null;
  full_deletion: boolean;
  pending_deletion_reason: string | null;
}

export async function listDocumentsInFolder(
  token: string,
  folderId: string
): Promise<DocumentSummary[]> {
  const response = await request(
    "document-service",
    `documents?folder_id=${encodeURIComponent(folderId)}`,
    {},
    token
  );
  return response.json();
}

export async function uploadDocument(
  token: string,
  params: {
    file: File;
    title: string;
    createdBy: string;
    folderId: string;
    objectTypeId?: number;
    attributes?: Record<string, string>;
  }
): Promise<DocumentSummary> {
  const formData = new FormData();
  formData.append("file", params.file);
  formData.append("title", params.title);
  formData.append("created_by", params.createdBy);
  formData.append("folder_id", params.folderId);
  if (params.objectTypeId !== undefined) {
    formData.append("object_type_id", String(params.objectTypeId));
    formData.append("attributes", JSON.stringify(params.attributes ?? {}));
  }

  const response = await request(
    "document-service",
    "documents",
    { method: "POST", body: formData },
    token
  );
  return response.json();
}

export async function updateDocumentMetadata(
  token: string,
  documentId: string,
  params: { title?: string; attributes?: Record<string, unknown> }
): Promise<DocumentSummary> {
  const response = await request(
    "document-service",
    `documents/${encodeURIComponent(documentId)}`,
    {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(params),
    },
    token
  );
  return response.json();
}

// Retention/legal hold/forced deletion (5.2/5.2a, since P7-S1).
export async function putDocumentRetention(
  token: string,
  documentId: string,
  params: {
    retentionUntil: string | null;
    fullDeletion: boolean;
    reason?: string | null;
    notifyEmail?: string | null;
  }
): Promise<DocumentSummary> {
  const response = await request(
    "document-service",
    `documents/${encodeURIComponent(documentId)}/retention`,
    {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        retention_until: params.retentionUntil,
        full_deletion: params.fullDeletion,
        reason: params.reason ?? null,
        notify_email: params.notifyEmail ?? null,
      }),
    },
    token
  );
  return response.json();
}

export async function restoreDocument(token: string, documentId: string): Promise<DocumentSummary> {
  const response = await request(
    "document-service",
    `documents/${encodeURIComponent(documentId)}/restore`,
    { method: "POST" },
    token
  );
  return response.json();
}

export async function listDeletedDocuments(
  token: string,
  folderId: string
): Promise<DocumentSummary[]> {
  const response = await request(
    "document-service",
    `documents/deleted?folder_id=${encodeURIComponent(folderId)}`,
    {},
    token
  );
  return response.json();
}

// Trash family (2.5, P15-S1) - installation-wide views. "admin" shows the
// regular, non-classified trash, "admin_classified" the structurally
// separate classified-documents trash (each with its own server-side
// checked role, 403 without it).
export type DocumentTrashScope = "personal" | "admin" | "admin_classified";

export async function listDeletedDocumentsGlobal(
  token: string,
  scope: DocumentTrashScope
): Promise<DocumentSummary[]> {
  const response = await request(
    "document-service",
    `documents/deleted?scope=${encodeURIComponent(scope)}`,
    {},
    token
  );
  return response.json();
}

export async function purgeDocument(token: string, documentId: string): Promise<void> {
  await request(
    "document-service",
    `documents/${encodeURIComponent(documentId)}/purge`,
    { method: "POST" },
    token
  );
}

// Quarantine area (2.5/10.3, P15-S2) - infected (or classified as such)
// uploads that never became a document (see virus-scan-service main.py).
// "released"/"purged" are terminal states, no further transition.
export interface ScanResult {
  id: string;
  document_id: string | null;
  filename: string;
  content_type: string | null;
  size_bytes: number;
  checksum_sha256: string;
  status: "clean" | "infected" | "released" | "purged";
  threat_name: string | null;
  engine: string;
  quarantine_object_key: string | null;
  created_by: string | null;
  scanned_at: string;
  resolved_by: string | null;
  resolved_at: string | null;
}

export async function listQuarantinedScans(token: string): Promise<ScanResult[]> {
  const response = await request("virus-scan-service", "scans?status=infected", {}, token);
  return response.json();
}

export async function releaseQuarantinedScan(
  token: string,
  scanId: string,
  params: {
    title: string;
    folderId?: string;
    objectTypeId?: number;
    attributes?: Record<string, unknown>;
  }
): Promise<ScanResult> {
  const response = await request(
    "virus-scan-service",
    `scans/${encodeURIComponent(scanId)}/release`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        title: params.title,
        folder_id: params.folderId ?? null,
        object_type_id: params.objectTypeId ?? null,
        attributes: params.attributes ?? {},
      }),
    },
    token
  );
  return response.json();
}

export async function purgeQuarantinedScan(token: string, scanId: string): Promise<void> {
  await request(
    "virus-scan-service",
    `scans/${encodeURIComponent(scanId)}/purge`,
    { method: "POST" },
    token
  );
}

// Inbox/outbox (2.5/3.3, P15-S3) - technical receipt/dispatch of external
// correspondence, review/assignment by the mailroom role (see mail-connector
// main.py).
export interface InboundAttachment {
  id: number;
  filename: string;
  content_type: string | null;
  size_bytes: number;
  scan_status: "clean" | "infected";
  scan_id: string;
  resulting_document_id: string | null;
}

export interface InboundMessage {
  id: string;
  from_address: string;
  subject: string;
  body_text: string;
  received_at: string;
  status: "unassigned" | "proposed_match" | "confirmed" | "rejected";
  match_type: "kennzeichen" | "vorgangsnummer" | null;
  match_value: string | null;
  proposed_target_type: "document" | "case" | null;
  proposed_target_id: string | null;
  match_candidates: string[];
  confirmed_by: string | null;
  confirmed_at: string | null;
  rejected_reason: string | null;
  attachments: InboundAttachment[];
}

export interface OutboundMessage {
  id: string;
  to_address: string;
  subject: string;
  body: string;
  related_document_id: string | null;
  related_case_id: string | null;
  sent_by: string;
  sent_at: string;
  status: "sent" | "failed";
  error_message: string | null;
}

export async function listInboundMessages(
  token: string,
  statusFilter?: string
): Promise<InboundMessage[]> {
  const path = statusFilter
    ? `inbound?status_filter=${encodeURIComponent(statusFilter)}`
    : "inbound";
  const response = await request("mail-connector", path, {}, token);
  return response.json();
}

export async function confirmInboundMatch(
  token: string,
  messageId: string,
  params: { title: string; folderId?: string }
): Promise<InboundMessage> {
  const response = await request(
    "mail-connector",
    `inbound/${encodeURIComponent(messageId)}/confirm-match`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ title: params.title, folder_id: params.folderId ?? null }),
    },
    token
  );
  return response.json();
}

export async function assignInboundMessage(
  token: string,
  messageId: string,
  params: { title: string; folderId: string; caseId?: string }
): Promise<InboundMessage> {
  const response = await request(
    "mail-connector",
    `inbound/${encodeURIComponent(messageId)}/assign`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        title: params.title,
        folder_id: params.folderId,
        case_id: params.caseId ?? null,
      }),
    },
    token
  );
  return response.json();
}

export async function rejectInboundMessage(
  token: string,
  messageId: string,
  reason?: string
): Promise<InboundMessage> {
  const response = await request(
    "mail-connector",
    `inbound/${encodeURIComponent(messageId)}/reject`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ reason: reason ?? null }),
    },
    token
  );
  return response.json();
}

export async function sendOutboundMessage(
  token: string,
  params: {
    toAddress: string;
    subject: string;
    body: string;
    relatedDocumentId?: string;
    relatedCaseId?: string;
  }
): Promise<OutboundMessage> {
  const response = await request(
    "mail-connector",
    "outbound",
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        to_address: params.toAddress,
        subject: params.subject,
        body: params.body,
        related_document_id: params.relatedDocumentId ?? null,
        related_case_id: params.relatedCaseId ?? null,
      }),
    },
    token
  );
  return response.json();
}

export async function listOutboundMessages(token: string): Promise<OutboundMessage[]> {
  const response = await request("mail-connector", "outbound", {}, token);
  return response.json();
}

// Contacts (2.5/4.4/7.4, P15-S4) - directory for finding other employees,
// always available locally, optionally across installations via the
// Federation Hub (see auth-service main.py).
export interface DirectoryEntry {
  id: string;
  username: string;
  email: string | null;
  first_name: string | null;
  last_name: string | null;
}

export interface FederatedDirectoryEntry extends DirectoryEntry {
  installation_id: string;
  installation_display_name: string;
}

export interface DirectoryFederationStatus {
  enabled: boolean;
  peer_installation_count: number;
}

export async function searchDirectory(token: string, q: string): Promise<DirectoryEntry[]> {
  const response = await request(
    "auth-service",
    `users/directory?q=${encodeURIComponent(q)}`,
    {},
    token
  );
  return response.json();
}

export async function getDirectoryFederationStatus(
  token: string
): Promise<DirectoryFederationStatus> {
  const response = await request("auth-service", "users/directory/federation-status", {}, token);
  return response.json();
}

export async function searchFederatedDirectory(
  token: string,
  q: string
): Promise<FederatedDirectoryEntry[]> {
  const response = await request(
    "auth-service",
    `users/directory/federated?q=${encodeURIComponent(q)}`,
    {},
    token
  );
  return response.json();
}

export interface LegalHold {
  id: string;
  document_id: string;
  reason: string | null;
  set_by: string;
  set_at: string;
  released_by: string | null;
  released_at: string | null;
}

export async function listLegalHolds(
  token: string,
  documentId: string,
  activeOnly = false
): Promise<LegalHold[]> {
  const response = await request(
    "document-service",
    `legal-holds?document_id=${encodeURIComponent(documentId)}&active_only=${activeOnly}`,
    {},
    token
  );
  return response.json();
}

export async function createLegalHold(
  token: string,
  params: { documentId: string; setBy: string; reason?: string | null }
): Promise<LegalHold> {
  const response = await request(
    "document-service",
    "legal-holds",
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        document_id: params.documentId,
        set_by: params.setBy,
        reason: params.reason ?? null,
      }),
    },
    token
  );
  return response.json();
}

export async function releaseLegalHold(
  token: string,
  holdId: string,
  releasedBy: string
): Promise<LegalHold> {
  const response = await request(
    "document-service",
    `legal-holds/${encodeURIComponent(holdId)}/release`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ released_by: releasedBy }),
    },
    token
  );
  return response.json();
}

export async function downloadDocument(token: string, documentId: string): Promise<Blob> {
  const response = await request(
    "document-service",
    `documents/${encodeURIComponent(documentId)}/content`,
    {},
    token
  );
  return response.blob();
}

export interface DocumentVersion {
  version_number: number;
  filename: string;
  content_type: string | null;
  size_bytes: number;
  checksum_sha256: string;
  is_conflict: boolean;
  based_on_version_number: number | null;
  comment: string | null;
  created_by: string;
  created_at: string;
}

// Version history (P5-S3, user request) - the backend has existed since
// document-service's check-in function, it just wasn't visible in the
// user UI until now.
export async function listDocumentVersions(
  token: string,
  documentId: string
): Promise<DocumentVersion[]> {
  const response = await request(
    "document-service",
    `documents/${encodeURIComponent(documentId)}/versions`,
    {},
    token
  );
  return response.json();
}

export async function downloadDocumentVersion(
  token: string,
  documentId: string,
  versionNumber: number
): Promise<Blob> {
  const response = await request(
    "document-service",
    `documents/${encodeURIComponent(documentId)}/versions/${versionNumber}/content`,
    {},
    token
  );
  return response.blob();
}

export interface RenditionSummary {
  id: string;
  document_id: string;
  version_number: number;
  rendition_type: string;
  source_filename: string;
  source_content_type: string | null;
  target_filename: string;
  target_content_type: string;
  size_bytes: number;
  status: "ready" | "failed";
  error_message: string | null;
  created_at: string;
  updated_at: string;
}

// Rendering Service (3.7/2.4, P5-S2) - generates surrogate representations/
// previews asynchronously after upload, so the list for a freshly uploaded
// document can initially be empty. `versionNumber` optionally filterable
// since P5-S3 (version selection in the preview).
export async function listRenditions(
  token: string,
  documentId: string,
  versionNumber?: number
): Promise<RenditionSummary[]> {
  const query = new URLSearchParams({ document_id: documentId });
  if (versionNumber !== undefined) query.set("version_number", String(versionNumber));
  const response = await request("rendering-service", `renditions?${query.toString()}`, {}, token);
  return response.json();
}

export async function downloadRenditionContent(token: string, renditionId: string): Promise<Blob> {
  const response = await request(
    "rendering-service",
    `renditions/${encodeURIComponent(renditionId)}/content`,
    {},
    token
  );
  return response.blob();
}

export interface OcrWord {
  text: string;
  left: number;
  top: number;
  width: number;
  height: number;
  confidence: number;
}

export interface OcrPage {
  page_number: number;
  width: number;
  height: number;
  words: OcrWord[];
}

export interface OcrResultSummary {
  id: string;
  document_id: string;
  version_number: number;
  status: "ready" | "needs_review" | "failed";
  engine: string;
  average_confidence: number;
  full_text: string;
  pages: OcrPage[];
  error_message: string | null;
  created_at: string;
  updated_at: string;
}

// OCR Service (3.9, P5-S3) - supplies word bounding boxes for
// position-accurate text overlay highlighting in the preview (user request).
export async function listOcrResults(
  token: string,
  documentId: string,
  versionNumber?: number
): Promise<OcrResultSummary[]> {
  const query = new URLSearchParams({ document_id: documentId });
  if (versionNumber !== undefined) query.set("version_number", String(versionNumber));
  const response = await request("ocr-service", `ocr-results?${query.toString()}`, {}, token);
  return response.json();
}

export async function downloadOcrPageImage(
  token: string,
  ocrResultId: string,
  pageNumber = 1
): Promise<Blob> {
  const response = await request(
    "ocr-service",
    `ocr-results/${encodeURIComponent(ocrResultId)}/page-image?page_number=${pageNumber}`,
    {},
    token
  );
  return response.blob();
}

// Signature Service (3.10, P6-S7) - electronic signature (SES/AES/QES),
// binds to the specific document version. Signing creates a new document
// version server-side (the PAdES signature changes the PDF bytes) - this
// session does not automatically update the version display elsewhere
// (e.g. PreviewPane), see docs/services/user-ui.md.
export type SignatureLevel = "ses" | "aes" | "qes";

export interface SignatureSummary {
  id: number;
  document_id: string;
  source_version_number: number;
  version_number: number;
  level: SignatureLevel;
  connector_id: string;
  signer_principal_id: string;
  signer_display_name: string;
  certificate_subject: string;
  certificate_serial: string;
  certificate_not_before: string;
  certificate_not_after: string;
  reason: string | null;
  signed_at: string;
}

export interface SignatureVerification {
  valid: boolean;
  integrity_intact: boolean;
  certificate_expired: boolean;
  errors: string[];
}

export async function listSignatures(
  token: string,
  documentId: string
): Promise<SignatureSummary[]> {
  const query = new URLSearchParams({ document_id: documentId });
  const response = await request("signature-service", `signatures?${query.toString()}`, {}, token);
  return response.json();
}

export async function createSignature(
  token: string,
  params: { documentId: string; level: SignatureLevel; signerPrincipalId: string }
): Promise<SignatureSummary> {
  const response = await request(
    "signature-service",
    "signatures",
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        document_id: params.documentId,
        level: params.level,
        signer_principal_id: params.signerPrincipalId,
      }),
    },
    token
  );
  return response.json();
}

export async function verifySignature(
  token: string,
  signatureId: number
): Promise<SignatureVerification> {
  const response = await request(
    "signature-service",
    `signatures/${signatureId}/verify`,
    {},
    token
  );
  return response.json();
}

// Search Service (3.7, P5-S4, ADR 0012: Postgres full-text search instead of
// a dedicated search index) - facets follow the object-type schema.
export interface SearchResult extends DocumentSummary {
  folder_name: string | null;
  rank: number;
  snippet: string;
}

export interface FacetObjectType {
  id: number;
  name: string;
  attributes: ObjectTypeAttribute[];
}

export interface SearchFacets {
  object_types: FacetObjectType[];
}

export interface SearchFacetCounts {
  folder: Array<{ folder_id: string | null; folder_name: string | null; count: number }>;
  object_type: Array<{ object_type_id: number | null; count: number }>;
}

export interface SearchResponse {
  results: SearchResult[];
  total_returned: number;
  facet_counts: SearchFacetCounts;
}

export async function getSearchFacets(token: string): Promise<SearchFacets> {
  const response = await request("search-service", "search/facets", {}, token);
  return response.json();
}

export interface SearchParams {
  q?: string;
  folderId?: string;
  objectTypeId?: number;
  createdBy?: string;
  createdAfter?: string;
  createdBefore?: string;
  // Keys already in the backend convention, e.g. "attr.kunde" or
  // "attr.betrag.gte" - see docs/services/search-service.md.
  attrFilters?: Record<string, string>;
  limit?: number;
  offset?: number;
}

export async function searchDocuments(token: string, params: SearchParams): Promise<SearchResponse> {
  const query = new URLSearchParams();
  if (params.q) query.set("q", params.q);
  if (params.folderId) query.set("folder_id", params.folderId);
  if (params.objectTypeId !== undefined) query.set("object_type_id", String(params.objectTypeId));
  if (params.createdBy) query.set("created_by", params.createdBy);
  if (params.createdAfter) query.set("created_after", params.createdAfter);
  if (params.createdBefore) query.set("created_before", params.createdBefore);
  for (const [key, value] of Object.entries(params.attrFilters ?? {})) {
    query.set(key, value);
  }
  if (params.limit !== undefined) query.set("limit", String(params.limit));
  if (params.offset !== undefined) query.set("offset", String(params.offset));
  const response = await request("search-service", `search?${query.toString()}`, {}, token);
  return response.json();
}

// Emergency shutdown (4.8, P6-S6) - pure status banner, no controls (only
// the activated superuser can act during the lockdown, 4.8).
export interface MaintenanceMode {
  active: boolean;
}

export async function getMaintenanceStatus(token: string): Promise<MaintenanceMode> {
  const response = await request("permission-service", "maintenance-mode", {}, token);
  return response.json();
}

// Deletion request workflow for regular users (5.2, since P7-S1c) - generic
// four-eyes mechanism of the Permission Service (4.3, since P6-S4), used
// here for the first time by the user UI itself (previously only
// `document.force_unlock`/`document.force_delete`/`folder.force_delete`,
// without any UI).
export interface ApprovalConfig {
  action_type: string;
  requires_approval: boolean;
  required_permission: string | null;
  updated_at: string;
}

export async function getApprovalConfig(token: string, actionType: string): Promise<ApprovalConfig> {
  const response = await request(
    "permission-service",
    `approval-config/${encodeURIComponent(actionType)}`,
    {},
    token
  );
  return response.json();
}

// Domain-separated admin roles (4.6) - system-native capabilities from the
// Permission Service, NOT from `user.realm_roles` (separate source, same
// pattern as `apps/admin-ui/src/lib/api.ts`'s function of the same name).
// First consumer in user-ui: RetentionPanel/FolderRetentionModal
// (post-roadmap Phase 19 Session 10, ADR 0075) show the legal-hold action
// button only for principals with `admin.legal_hold`.
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
  params: { actionType: string; status?: string }
): Promise<ApprovalRequest[]> {
  const query = new URLSearchParams({ action_type: params.actionType });
  if (params.status) query.set("status", params.status);
  const response = await request(
    "permission-service",
    `approval-requests?${query.toString()}`,
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
    `approval-requests/${encodeURIComponent(requestId)}/approve`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ approved_by: approvedBy }),
    },
    token
  );
  return response.json();
}

export async function rejectApprovalRequest(
  token: string,
  requestId: string,
  rejectedBy: string,
  reason?: string | null
): Promise<ApprovalRequest> {
  const response = await request(
    "permission-service",
    `approval-requests/${encodeURIComponent(requestId)}/reject`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ rejected_by: rejectedBy, reason: reason ?? null }),
    },
    token
  );
  return response.json();
}

// Single-item retrieval (since P7-S1d) - both endpoints have existed for a
// long time, just no frontend function for them until now. Needed by
// `FavoritesPane`/the favorite-open path to resolve display names, or (for
// folders) the parent chain up to the root.
export async function getDocument(token: string, documentId: string): Promise<DocumentSummary> {
  const response = await request(
    "document-service",
    `documents/${encodeURIComponent(documentId)}`,
    {},
    token
  );
  return response.json();
}

export async function getFolder(token: string, folderId: string): Promise<Folder> {
  const response = await request("folder-service", `folders/${encodeURIComponent(folderId)}`, {}, token);
  return response.json();
}

// Favorites/watch list (quick retrieval, since P7-S1d) - new, deliberately
// decoupled `favorite-service` without referential checks against
// document-/folder-service (see docs/services/favorite-service.md).
export interface Favorite {
  id: string;
  user_id: string;
  object_type: "document" | "folder";
  object_id: string;
  created_at: string;
}

export async function listFavorites(
  token: string,
  userId: string,
  objectType?: "document" | "folder"
): Promise<Favorite[]> {
  const query = new URLSearchParams({ user_id: userId });
  if (objectType) query.set("object_type", objectType);
  const response = await request("favorite-service", `favorites?${query.toString()}`, {}, token);
  return response.json();
}

export async function addFavorite(
  token: string,
  params: { user_id: string; object_type: "document" | "folder"; object_id: string }
): Promise<Favorite> {
  const response = await request(
    "favorite-service",
    "favorites",
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(params),
    },
    token
  );
  return response.json();
}

export async function removeFavorite(
  token: string,
  params: { user_id: string; object_type: "document" | "folder"; object_id: string }
): Promise<void> {
  const query = new URLSearchParams(params);
  await request("favorite-service", `favorites?${query.toString()}`, { method: "DELETE" }, token);
}

// User resolution by exact username (2.5, P14-S6) - deliberately NOT gated
// behind `admin.user_management` (unlike a full user list): every
// authenticated user may resolve a single account, e.g. to invite them to a
// teamspace. `X-DMS-Principal` is the Keycloak `sub` UUID, which no user
// knows by heart - this call translates the typed username into exactly
// that UUID.
export interface UserLookup {
  id: string;
  username: string;
}

export async function lookupUserByUsername(
  token: string,
  username: string
): Promise<UserLookup> {
  const response = await request(
    "auth-service",
    `users/lookup?username=${encodeURIComponent(username)}`,
    {},
    token
  );
  return response.json();
}

// Reverse identity resolution (post-roadmap Phase 19 Session 4, ADR 0069) -
// counterpart to lookupUserByUsername: delegations/teamspace member lists
// only know the raw principal_id UUID; this call translates it back into a
// username for display. Same gate as the forward resolution (`users.lookup`
// via the "everyone" group in permission-service).
export async function lookupUserById(token: string, userId: string): Promise<UserLookup> {
  const response = await request(
    "auth-service",
    `users/${encodeURIComponent(userId)}`,
    {},
    token
  );
  return response.json();
}

// Team workspace "Teamspace" (2.5, P14-S6) - self-managed, persistent group
// area (folders/documents/appointments/contacts), new `teamspace-service`.
// Its own access regime, independent of the rest of RBAC (see
// docs/services/teamspace-service.md) - every call here implicitly carries
// `X-DMS-Principal` via the bearer token forwarded by the gateway;
// `teamspace-service` checks membership itself.
export interface Teamspace {
  id: string;
  name: string;
  description: string;
  root_folder_id: string;
  created_by: string;
  created_at: string;
  updated_at: string;
}

export interface TeamspaceMember {
  id: number;
  teamspace_id: string;
  principal_id: string;
  can_manage_members: boolean;
  invited_by: string;
  invited_at: string;
}

export interface TeamspaceAppointment {
  id: number;
  teamspace_id: string;
  title: string;
  description: string;
  start_at: string;
  end_at: string;
  created_by: string;
  created_at: string;
}

export interface TeamspaceContact {
  id: number;
  teamspace_id: string;
  name: string;
  email: string | null;
  phone: string | null;
  note: string;
  created_by: string;
  created_at: string;
}

export async function createTeamspace(
  token: string,
  params: { name: string; description?: string }
): Promise<Teamspace> {
  const response = await request(
    "teamspace-service",
    "teamspaces",
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name: params.name, description: params.description ?? "" }),
    },
    token
  );
  return response.json();
}

export async function listTeamspaces(token: string): Promise<Teamspace[]> {
  const response = await request("teamspace-service", "teamspaces", {}, token);
  return response.json();
}

export async function deleteTeamspace(token: string, teamspaceId: string): Promise<void> {
  await request(
    "teamspace-service",
    `teamspaces/${encodeURIComponent(teamspaceId)}`,
    { method: "DELETE" },
    token
  );
}

export async function listTeamspaceMembers(
  token: string,
  teamspaceId: string
): Promise<TeamspaceMember[]> {
  const response = await request(
    "teamspace-service",
    `teamspaces/${encodeURIComponent(teamspaceId)}/members`,
    {},
    token
  );
  return response.json();
}

export async function inviteTeamspaceMember(
  token: string,
  teamspaceId: string,
  params: { principalId: string; canManageMembers?: boolean }
): Promise<TeamspaceMember> {
  const response = await request(
    "teamspace-service",
    `teamspaces/${encodeURIComponent(teamspaceId)}/members`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        principal_id: params.principalId,
        can_manage_members: params.canManageMembers ?? false,
      }),
    },
    token
  );
  return response.json();
}

export async function removeTeamspaceMember(
  token: string,
  teamspaceId: string,
  principalId: string
): Promise<void> {
  await request(
    "teamspace-service",
    `teamspaces/${encodeURIComponent(teamspaceId)}/members/${encodeURIComponent(principalId)}`,
    { method: "DELETE" },
    token
  );
}

export async function listTeamspaceAppointments(
  token: string,
  teamspaceId: string
): Promise<TeamspaceAppointment[]> {
  const response = await request(
    "teamspace-service",
    `teamspaces/${encodeURIComponent(teamspaceId)}/appointments`,
    {},
    token
  );
  return response.json();
}

export async function createTeamspaceAppointment(
  token: string,
  teamspaceId: string,
  params: { title: string; description?: string; startAt: string; endAt: string }
): Promise<TeamspaceAppointment> {
  const response = await request(
    "teamspace-service",
    `teamspaces/${encodeURIComponent(teamspaceId)}/appointments`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        title: params.title,
        description: params.description ?? "",
        start_at: params.startAt,
        end_at: params.endAt,
      }),
    },
    token
  );
  return response.json();
}

export async function deleteTeamspaceAppointment(
  token: string,
  teamspaceId: string,
  appointmentId: number
): Promise<void> {
  await request(
    "teamspace-service",
    `teamspaces/${encodeURIComponent(teamspaceId)}/appointments/${appointmentId}`,
    { method: "DELETE" },
    token
  );
}

export async function listTeamspaceContacts(
  token: string,
  teamspaceId: string
): Promise<TeamspaceContact[]> {
  const response = await request(
    "teamspace-service",
    `teamspaces/${encodeURIComponent(teamspaceId)}/contacts`,
    {},
    token
  );
  return response.json();
}

export async function createTeamspaceContact(
  token: string,
  teamspaceId: string,
  params: { name: string; email?: string; phone?: string; note?: string }
): Promise<TeamspaceContact> {
  const response = await request(
    "teamspace-service",
    `teamspaces/${encodeURIComponent(teamspaceId)}/contacts`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        name: params.name,
        email: params.email || null,
        phone: params.phone || null,
        note: params.note ?? "",
      }),
    },
    token
  );
  return response.json();
}

export async function deleteTeamspaceContact(
  token: string,
  teamspaceId: string,
  contactId: number
): Promise<void> {
  await request(
    "teamspace-service",
    `teamspaces/${encodeURIComponent(teamspaceId)}/contacts/${contactId}`,
    { method: "DELETE" },
    token
  );
}

// Public share link (4.2a, P14-S10) - time-limited, unauthenticated read
// access to exactly one document. The two `public/...` calls below
// deliberately pass NO token (the last parameter of `request()` is omitted) -
// they go through the gateway's own `public_routes` exception list, the
// share-link token itself travels along as a query parameter, see
// docs/services/gateway-service.md.

export interface ShareLinkConfig {
  enabled: boolean;
  max_validity_days: number;
  updated_at: string;
}

export async function getShareLinkConfig(token: string): Promise<ShareLinkConfig> {
  const response = await request("document-service", "share-link-config", {}, token);
  return response.json();
}

export interface ShareLink {
  token: string;
  document_id: string;
  created_by: string;
  created_at: string;
  expires_at: string;
  revoked_at: string | null;
  revoked_by: string | null;
}

export async function listShareLinks(token: string, documentId: string): Promise<ShareLink[]> {
  const response = await request(
    "document-service",
    `documents/${encodeURIComponent(documentId)}/share-links`,
    {},
    token
  );
  return response.json();
}

export async function createShareLink(
  token: string,
  documentId: string,
  expiresAt: string
): Promise<ShareLink> {
  const response = await request(
    "document-service",
    `documents/${encodeURIComponent(documentId)}/share-links`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ expires_at: expiresAt }),
    },
    token
  );
  return response.json();
}

export async function revokeShareLink(token: string, shareToken: string): Promise<void> {
  await request(
    "document-service",
    `share-links/${encodeURIComponent(shareToken)}`,
    { method: "DELETE" },
    token
  );
}

export interface PublicShareLink {
  title: string;
  content_type: string | null;
  size_bytes: number;
  expires_at: string;
}

export async function getPublicShareLink(shareToken: string): Promise<PublicShareLink> {
  const response = await request(
    "document-service",
    `public/share-links?token=${encodeURIComponent(shareToken)}`
  );
  return response.json();
}

export function publicShareLinkContentUrl(shareToken: string): string {
  return `${GATEWAY_BASE_URL}/api/document-service/public/share-links/content?token=${encodeURIComponent(
    shareToken
  )}`;
}

// Direct Office editing (post-roadmap feature): launch Word/Excel/PowerPoint
// directly from the browser via the Office URI scheme
// (`ms-word:ofe|u|<url>` etc.) against webdav-connector. The content-type →
// scheme/extension mapping doesn't exist anywhere else in the project yet,
// it's only needed here.
const OFFICE_LAUNCH_MAP: Record<string, { scheme: string; ext: string; label: string }> = {
  "application/msword": { scheme: "ms-word", ext: "doc", label: "Word" },
  "application/vnd.openxmlformats-officedocument.wordprocessingml.document": {
    scheme: "ms-word",
    ext: "docx",
    label: "Word",
  },
  "application/vnd.ms-excel": { scheme: "ms-excel", ext: "xls", label: "Excel" },
  "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": {
    scheme: "ms-excel",
    ext: "xlsx",
    label: "Excel",
  },
  "application/vnd.ms-powerpoint": { scheme: "ms-powerpoint", ext: "ppt", label: "PowerPoint" },
  "application/vnd.openxmlformats-officedocument.presentationml.presentation": {
    scheme: "ms-powerpoint",
    ext: "pptx",
    label: "PowerPoint",
  },
};

export function officeLaunchInfo(
  contentType: string | null | undefined
): { scheme: string; ext: string; label: string } | null {
  if (!contentType) return null;
  return OFFICE_LAUNCH_MAP[contentType] ?? null;
}

export interface WebdavEditToken {
  token: string;
  expires_at: string;
}

export async function createWebdavEditToken(
  token: string,
  documentId: string
): Promise<WebdavEditToken> {
  const response = await request(
    "document-service",
    `documents/${encodeURIComponent(documentId)}/webdav-edit-tokens`,
    { method: "POST" },
    token
  );
  return response.json();
}

// Embeds the edit token as the basic-auth "username" with an empty password
// in the URL - `DmsAuthDomainController` uses this to distinguish an edit
// token from a real WebDAV mount (which always sends a real password),
// without needing to know the token format itself.
export function officeLaunchUrl(webdavToken: string, documentId: string, ext: string): string {
  const host = new URL(WEBDAV_CONNECTOR_BASE_URL).host;
  const protocol = new URL(WEBDAV_CONNECTOR_BASE_URL).protocol;
  return `${protocol}//${encodeURIComponent(webdavToken)}:@${host}/webdav/by-id/${encodeURIComponent(
    documentId
  )}.${ext}`;
}

// Deputy delegation for absences (4.4a, P14-S11) - self-managed,
// time-limited transfer of task responsibility, new `permission-service`
// record (not a dedicated service, see docs/services/permission-service.md).
// `delegator_principal_id` is always the requesting person themselves
// (derived server-side from `X-DMS-Principal`) - no field for it in the
// request body.
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

export async function createDelegation(
  token: string,
  params: { deputyPrincipalId: string; startsAt: string; endsAt: string }
): Promise<Delegation> {
  const response = await request(
    "permission-service",
    "delegations",
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        deputy_principal_id: params.deputyPrincipalId,
        starts_at: params.startsAt,
        ends_at: params.endsAt,
      }),
    },
    token
  );
  return response.json();
}

export async function listMyDelegations(
  token: string,
  delegatorPrincipalId: string
): Promise<Delegation[]> {
  const response = await request(
    "permission-service",
    `delegations?delegator_principal_id=${encodeURIComponent(delegatorPrincipalId)}`,
    {},
    token
  );
  return response.json();
}

export async function listActiveDelegationsForDeputy(
  token: string,
  principalId: string
): Promise<Delegation[]> {
  const response = await request(
    "permission-service",
    `delegations/active-for-deputy/${encodeURIComponent(principalId)}`,
    {},
    token
  );
  return response.json();
}

export async function revokeDelegation(token: string, delegationId: string): Promise<void> {
  await request(
    "permission-service",
    `delegations/${encodeURIComponent(delegationId)}`,
    { method: "DELETE" },
    token
  );
}

// Records-disposal access area (2.5/5.6, P15-S5) - documents/case folders
// that have already been disposed of but are still within the transition
// period.
export interface ReleasedItem {
  transfer_id: string;
  kind: "document" | "case";
  subject_id: string;
  title: string;
  identifier: string | null;
  released_at: string | null;
  purge_at: string | null;
}

export async function listReleasedItems(token: string, q?: string): Promise<ReleasedItem[]> {
  const path = q ? `released-items?q=${encodeURIComponent(q)}` : "released-items";
  const response = await request("archival-service", path, {}, token);
  return response.json();
}

export async function retrieveArchivalTransfer(token: string, transferId: string): Promise<void> {
  await request(
    "archival-service",
    `archival-transfers/${encodeURIComponent(transferId)}/retrieve`,
    { method: "POST" },
    token
  );
}

export async function downloadCaseArchivalPackage(
  token: string,
  transferId: string
): Promise<Blob> {
  const response = await request(
    "archival-service",
    `case-archival-transfers/${encodeURIComponent(transferId)}/package`,
    {},
    token
  );
  return response.blob();
}

// Structure templates (2.5/7.3, P15-S6) - a folder subtree as a named,
// reusable template (e.g. a file-plan skeleton).
export interface FolderTemplate {
  id: string;
  name: string;
  description: string | null;
  created_by: string;
  created_at: string;
}

export interface FolderTemplateNode {
  name: string;
  object_type_id: number | null;
  children: FolderTemplateNode[];
}

export interface FolderTemplateDetail extends FolderTemplate {
  structure: FolderTemplateNode;
}

export interface FolderTemplateApplyResult {
  root_folder: Folder;
  created_count: number;
}

export async function listFolderTemplates(token: string): Promise<FolderTemplate[]> {
  const response = await request("folder-service", "folder-templates", {}, token);
  return response.json();
}

export async function getFolderTemplate(
  token: string,
  templateId: string
): Promise<FolderTemplateDetail> {
  const response = await request(
    "folder-service",
    `folder-templates/${encodeURIComponent(templateId)}`,
    {},
    token
  );
  return response.json();
}

export async function createFolderTemplate(
  token: string,
  params: { sourceFolderId: string; name: string; description?: string; createdBy: string }
): Promise<FolderTemplate> {
  const response = await request(
    "folder-service",
    "folder-templates",
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        source_folder_id: params.sourceFolderId,
        name: params.name,
        description: params.description || null,
        created_by: params.createdBy,
      }),
    },
    token
  );
  return response.json();
}

export async function deleteFolderTemplate(token: string, templateId: string): Promise<void> {
  await request(
    "folder-service",
    `folder-templates/${encodeURIComponent(templateId)}`,
    { method: "DELETE" },
    token
  );
}

export async function applyFolderTemplate(
  token: string,
  templateId: string,
  params: { targetParentId: string; createdBy: string }
): Promise<FolderTemplateApplyResult> {
  const response = await request(
    "folder-service",
    `folder-templates/${encodeURIComponent(templateId)}/apply`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        target_parent_id: params.targetParentId,
        created_by: params.createdBy,
      }),
    },
    token
  );
  return response.json();
}
