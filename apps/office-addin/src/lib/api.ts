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

// Every call goes through the gateway (3.5): /api/{service_type}/{path} instead
// of direct backend addresses. This means the taskpane talks to the SAME
// existing REST endpoints as user-ui (3.3a: "the same already
// existing ... API path" instead of its own document logic) - no new
// backend service/endpoint for this session.
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

// --- Documents (document-service) ------------------------------------------

export interface DocumentSummary {
  id: string;
  title: string;
  folder_id: string | null;
  object_type_id: number | null;
  attributes: Record<string, unknown>;
  current_version_number: number;
  created_by: string;
  created_at: string;
  updated_at: string;
}

export async function getDocument(token: string, documentId: string): Promise<DocumentSummary> {
  const response = await request(
    "document-service",
    `documents/${encodeURIComponent(documentId)}`,
    {},
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

export async function downloadDocumentContent(token: string, documentId: string): Promise<Blob> {
  const response = await request(
    "document-service",
    `documents/${encodeURIComponent(documentId)}/content`,
    {},
    token
  );
  return response.blob();
}

// Check in a new version of a document already linked to the taskpane
// ("Save to OG Doc") - identical endpoint/conflict protection
// (`expected_base_version_number`) as any other check-in client.
export interface CheckinResult {
  version: { version_number: number };
  is_conflict: boolean;
}

export async function checkinVersion(
  token: string,
  documentId: string,
  params: { file: Blob; expectedBaseVersionNumber: number; createdBy: string; comment?: string }
): Promise<CheckinResult> {
  const formData = new FormData();
  formData.append("file", params.file, "document.docx");
  formData.append("expected_base_version_number", String(params.expectedBaseVersionNumber));
  formData.append("created_by", params.createdBy);
  if (params.comment) formData.append("comment", params.comment);

  const response = await request(
    "document-service",
    `documents/${encodeURIComponent(documentId)}/versions`,
    { method: "POST", body: formData },
    token
  );
  return response.json();
}

// Create a new document ("New from template" - first save after
// loading a template into an empty Word document). `derived_from_document_id`
// already exists in the schema (provenance tracking, document-service),
// and is actually set by a frontend for the first time here.
export async function createDocument(
  token: string,
  params: {
    file: Blob;
    title: string;
    createdBy: string;
    objectTypeId?: number;
    attributes?: Record<string, unknown>;
    derivedFromDocumentId?: string;
    derivedFromVersionNumber?: number;
  }
): Promise<DocumentSummary> {
  const formData = new FormData();
  formData.append("file", params.file, "document.docx");
  formData.append("title", params.title);
  formData.append("created_by", params.createdBy);
  if (params.objectTypeId !== undefined) {
    formData.append("object_type_id", String(params.objectTypeId));
    formData.append("attributes", JSON.stringify(params.attributes ?? {}));
  }
  if (params.derivedFromDocumentId) {
    formData.append("derived_from_document_id", params.derivedFromDocumentId);
  }
  if (params.derivedFromVersionNumber !== undefined) {
    formData.append("derived_from_version_number", String(params.derivedFromVersionNumber));
  }

  const response = await request(
    "document-service",
    "documents",
    { method: "POST", body: formData },
    token
  );
  return response.json();
}

// --- Locks (check-out/check-in, document-service) -------------------------
//
// Unlike user-ui (which uses purely optimistic version checking on check-in,
// ADR 0002), the taskpane uses the already-existing explicit lock that no
// frontend has used so far: a Word editing session
// can take a long time, so "someone else is currently editing this" is a
// genuine, meaningful hint here rather than just a conflict on save.
export interface DocumentLock {
  document_id: string;
  locked_by: string;
  session_id: string;
  based_on_version_number: number;
  expires_at: string;
}

export async function acquireLock(
  token: string,
  documentId: string,
  params: { lockedBy: string; sessionId: string }
): Promise<DocumentLock> {
  const response = await request(
    "document-service",
    `documents/${encodeURIComponent(documentId)}/lock`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ locked_by: params.lockedBy, session_id: params.sessionId }),
    },
    token
  );
  return response.json();
}

export async function releaseLock(
  token: string,
  documentId: string,
  releasedBy: string
): Promise<void> {
  await request(
    "document-service",
    `documents/${encodeURIComponent(documentId)}/lock`,
    {
      method: "DELETE",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ released_by: releasedBy }),
    },
    token
  );
}

// --- Folders/template library (folder-service) -----------------------------

export interface Folder {
  id: string;
  name: string;
  parent_id: string | null;
}

export async function listRootFolders(token: string): Promise<Folder[]> {
  const response = await request("folder-service", "folders/root/children", {}, token);
  return response.json();
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

// --- Object types (object-type-service) - for the inline metadata form -----

export interface ObjectTypeAttribute {
  name: string;
  type?: string;
  required?: boolean;
}

export interface ObjectType {
  id: number;
  name: string;
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

// --- Search (search-service) - document picker "Open from OG Doc" -----------

export interface SearchResult {
  id: string;
  title: string;
  folder_name: string | null;
}

export async function searchDocuments(token: string, query: string): Promise<SearchResult[]> {
  const response = await request(
    "search-service",
    `search?q=${encodeURIComponent(query)}&limit=10`,
    {},
    token
  );
  const body = (await response.json()) as { results: SearchResult[] };
  return body.results;
}

// --- Workflow Engine (workflow-service, 7.1) --------------------------------

export interface ProcessDefinition {
  id: number;
  name: string;
  version: number;
}

export async function listProcessDefinitions(token: string): Promise<ProcessDefinition[]> {
  const response = await request("workflow-service", "process-definitions", {}, token);
  return response.json();
}

export interface ProcessInstance {
  id: string;
  process_definition_id: number;
  business_key: string | null;
  status: string;
}

// `business_key` = the DMS document ID - the same correlation used to bind
// every other process in this system to a business object
// (no new mechanism).
export async function listInstancesForDocument(
  token: string,
  documentId: string
): Promise<ProcessInstance[]> {
  const response = await request(
    "workflow-service",
    `instances?business_key=${encodeURIComponent(documentId)}`,
    {},
    token
  );
  return response.json();
}

export async function startInstance(
  token: string,
  processDefinitionId: number,
  params: { createdBy: string; businessKey: string }
): Promise<ProcessInstance> {
  const response = await request(
    "workflow-service",
    `process-definitions/${processDefinitionId}/instances`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ created_by: params.createdBy, business_key: params.businessKey }),
    },
    token
  );
  return response.json();
}

export interface ReadyTask {
  id: string;
  name: string;
  lane: string | null;
  data: Record<string, unknown>;
  extensions: Record<string, string>;
}

export async function listInstanceTasks(
  token: string,
  instanceId: string
): Promise<ReadyTask[]> {
  const response = await request(
    "workflow-service",
    `instances/${encodeURIComponent(instanceId)}/tasks`,
    {},
    token
  );
  return response.json();
}

export async function completeTask(
  token: string,
  params: { instanceId: string; taskId: string; completedBy: string }
): Promise<void> {
  await request(
    "workflow-service",
    `instances/${params.instanceId}/tasks/${params.taskId}/complete`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ completed_by: params.completedBy, data: {} }),
    },
    token
  );
}
