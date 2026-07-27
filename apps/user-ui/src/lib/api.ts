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
  params: { name: string; parentId: string; createdBy: string }
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
  params: { file: File; title: string; createdBy: string; folderId: string }
): Promise<DocumentSummary> {
  const formData = new FormData();
  formData.append("file", params.file);
  formData.append("title", params.title);
  formData.append("created_by", params.createdBy);
  formData.append("folder_id", params.folderId);

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

export async function downloadOcrPageImage(token: string, ocrResultId: string): Promise<Blob> {
  const response = await request(
    "ocr-service",
    `ocr-results/${encodeURIComponent(ocrResultId)}/page-image`,
    {},
    token
  );
  return response.blob();
}
