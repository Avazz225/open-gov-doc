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
  // Persönlicher Papierkorb (2.5, P15-S1).
  deleted_by: string | null;
  // Aufbewahrung/Zwangslöschung für Ordner (5.2/5.2a, seit P7-S1b).
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

// Sammelbearbeitung von Metadaten (8, P14-S12) - Ordner-Pendant zu
// `updateDocumentMetadata`, bislang gab es dafür keine eigene Funktion (nur
// `renameFolder`, das ausschließlich `name` setzt). Gleiche Full-Replace-
// Semantik wie beim Dokument-Pendant - `attributes` ersetzt den gesamten
// bestehenden Wert, kein Merge serverseitig.
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

// Löschantrag-Workflow für reguläre Nutzer (5.2, seit P7-S1c) - zwei
// mögliche Ausgänge, gleiches Wrapper-Muster für Ordner und Dokumente:
// sofort ausgeführt, oder per Vier-Augen-Prinzip zurückgestellt (Aktionstyp
// `folder.delete`/`document.delete`, unabhängig von der bereits
// bestehenden retentionsgetriggerten Zwangslöschung).
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

// Papierkorb-Familie (2.5, P15-S1) - installationsweite Sichten statt der
// obigen ordnerbezogenen Auflistung. "personal" braucht keine Rolle (nur die
// eigenen Löschmarkierungen), "admin" verlangt serverseitig die
// Löschadministration-Rolle (403 sonst).
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
  // Kennzeichengenerator (2.2, seit P5e-S1/S3) - nur für applies_to="document"
  // gesetzt. kennzeichen_display_override ist ein Tri-State: null/undefined =
  // globaler Standard (KennzeichenConfig) gilt, siehe lib/kennzeichen.ts.
  kennzeichen_format?: string | null;
  kennzeichen_display_override?: boolean | null;
  // Verschlusssachen-Kennzeichnung (2.5, P15-S1) - nur für applies_to="document".
  is_classified?: boolean;
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

// Formular-Layouts (2.2b, seit P5b-S2/ADR 0014) - steuern seit P5b-S4 die
// Anordnung der Attributfelder in Metadaten-Panel, Suchmaske und Upload-
// Dialog. `is_custom: false` heißt "generiertes Smart Layout, nicht
// gespeichert", `true` heißt "über den Admin-UI-Layout-Designer gespeichertes
// Override" - für die reine Anzeige hier ohne Bedeutung, nur zur Vollständigkeit
// mit übernommen.
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
  // Persönlicher Papierkorb (2.5, P15-S1).
  deleted_by: string | null;
  created_by: string;
  created_at: string;
  updated_at: string;
  // Aufbewahrung/Zwangslöschung (5.2/5.2a, seit P7-S1).
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

// Aufbewahrung/Legal Hold/Zwangslöschung (5.2/5.2a, seit P7-S1).
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

// Papierkorb-Familie (2.5, P15-S1) - installationsweite Sichten. "admin"
// zeigt den regulären, nicht-klassifizierten Papierkorb, "admin_classified"
// den strukturell getrennten Verschlusssachen-Papierkorb (je eigene,
// serverseitig geprüfte Rolle, 403 ohne).
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

// Quarantäne-Bereich (2.5/10.3, P15-S2) - infizierte (oder als solche
// eingestufte) Uploads, die nie ein Dokument wurden (siehe virus-scan-service
// main.py). "released"/"purged" sind Endzustände, kein weiterer Übergang.
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

// Posteingang/Postausgang (2.5/3.3, P15-S3) - technischer Empfang/Versand
// externer Korrespondenz, Sichtung/Zuordnung durch die Poststelle-Rolle
// (siehe mail-connector main.py).
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

// Kontakte (2.5/4.4/7.4, P15-S4) - Verzeichnis zum Auffinden anderer
// Mitarbeitender, lokal immer verfügbar, optional installationsübergreifend
// über den Federation Hub (siehe auth-service main.py).
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

// Versionshistorie (P5-S3, Nutzerwunsch) - Backend existiert bereits seit
// document-service's Check-in-Funktion, war im User-UI bisher nicht sichtbar.
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

// Rendering Service (3.7/2.4, P5-S2) - erzeugt Ersatzdarstellungen/Vorschauen
// asynchron nach dem Upload, daher kann die Liste für ein frisch hochgeladenes
// Dokument zunächst leer sein. `versionNumber` seit P5-S3 (Versionsauswahl in
// der Vorschau) optional filterbar.
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

// OCR Service (3.9, P5-S3) - liefert Wort-Bounding-Boxen für die
// positionsgenaue Text-Overlay-Markierung in der Vorschau (Nutzerwunsch).
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

// Signature Service (3.10, P6-S7) - elektronische Signatur (SES/AES/QES),
// bindet sich an die konkrete Dokumentversion. Signieren erzeugt serverseitig
// eine neue Dokumentversion (die PAdES-Signatur verändert die PDF-Bytes) -
// diese Session aktualisiert die Versionsanzeige an anderer Stelle (z. B.
// PreviewPane) nicht automatisch, siehe docs/services/user-ui.md.
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

// Search Service (3.7, P5-S4, ADR 0012: Postgres Volltextsuche statt
// dediziertem Suchindex) - Facetten orientieren sich am Objekttyp-Schema.
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
  // Schlüssel bereits in der Backend-Konvention, z. B. "attr.kunde" oder
  // "attr.betrag.gte" - siehe docs/services/search-service.md.
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

// Not-Shutdown (4.8, P6-S6) - reines Status-Banner, keine Bedienelemente
// (nur der aktivierte Superuser ist während der Sperre handlungsfähig, 4.8).
export interface MaintenanceMode {
  active: boolean;
}

export async function getMaintenanceStatus(token: string): Promise<MaintenanceMode> {
  const response = await request("permission-service", "maintenance-mode", {}, token);
  return response.json();
}

// Löschantrag-Workflow für reguläre Nutzer (5.2, seit P7-S1c) - generischer
// Vier-Augen-Mechanismus des Permission Service (4.3, seit P6-S4), hier zum
// ersten Mal von der User-UI selbst genutzt (bisher nur `document.force_
// unlock`/`document.force_delete`/`folder.force_delete`, ohne jede UI).
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

// Einzelabruf (seit P7-S1d) - beide Endpunkte existieren bereits seit
// langem, nur bislang keine Frontend-Funktion dafür. Werden von
// `FavoritesPane`/dem Favoriten-Öffnen-Pfad gebraucht, um Anzeigenamen bzw.
// (bei Ordnern) die Eltern-Kette bis zur Wurzel aufzulösen.
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

// Favoriten/Merkliste (schnelles Wiederfinden, seit P7-S1d) - neuer, bewusst
// entkoppelter `favorite-service` ohne referenzielle Prüfung gegen
// document-/folder-service (siehe docs/services/favorite-service.md).
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

// Nutzer-Auflösung per exaktem Username (2.5, P14-S6) - bewusst NICHT hinter
// `admin.user_management` gegated (anders als eine vollständige Nutzerliste):
// jeder authentifizierte Nutzer darf einen einzelnen Account auflösen, um ihn
// z. B. in einen Teamspace einzuladen. `X-DMS-Principal` ist die Keycloak-
// `sub`-UUID, kein Nutzer kennt sie auswendig - dieser Aufruf übersetzt den
// eingetippten Username in genau diese UUID.
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

// Team-Arbeitsbereich "Teamspace" (2.5, P14-S6) - selbstverwalteter,
// dauerhafter Gruppenbereich (Ordner/Dokumente/Termine/Kontakte), neuer
// `teamspace-service`. Eigenes, von der übrigen RBAC unabhängiges
// Zugriffsregime (siehe docs/services/teamspace-service.md) - jeder Aufruf
// hier trägt implizit `X-DMS-Principal` über den vom Gateway weitergereichten
// Bearer-Token, `teamspace-service` prüft die Mitgliedschaft selbst.
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

// Öffentlicher Freigabelink (4.2a, P14-S10) - zeitlich begrenzter,
// unauthentifizierter Lesezugriff auf genau ein Dokument. Die beiden
// `public/...`-Aufrufe unten übergeben bewusst KEIN Token (letzter Parameter
// von `request()` bleibt weg) - sie laufen über die gateway-eigene
// `public_routes`-Ausnahmeliste, das Freigabelink-Token selbst reist als
// Query-Parameter mit, siehe docs/services/gateway-service.md.

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

// Stellvertretung bei Abwesenheit (4.4a, P14-S11) - selbstverwaltete,
// zeitlich befristete Übertragung der Aufgabenwahrnehmung, neuer
// `permission-service`-Datensatz (kein eigener Service, siehe
// docs/services/permission-service.md). `delegator_principal_id` ist immer
// die anfragende Person selbst (server-seitig aus `X-DMS-Principal`
// abgeleitet) - kein Feld dafür im Request-Body.
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
