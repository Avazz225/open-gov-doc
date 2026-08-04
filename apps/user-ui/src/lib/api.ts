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

export async function deleteFolder(token: string, folderId: string): Promise<void> {
  await request(
    "folder-service",
    `folders/${encodeURIComponent(folderId)}`,
    { method: "DELETE" },
    token
  );
}

// Aufbewahrung/Legal Hold/Zwangslöschung für Ordner (5.2/5.2a, seit
// P7-S1b) - 1:1 dasselbe Muster wie die Dokument-Pendants weiter unten, nur
// gegen `folder-service`-Endpunkte.
export async function trashFolder(
  token: string,
  folderId: string,
  deletedBy: string
): Promise<Folder> {
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
