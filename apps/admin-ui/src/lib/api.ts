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

export interface ObjectType {
  id: number;
  name: string;
  applies_to: string;
  attributes: Array<Record<string, unknown>>;
  naming_constraints: Record<string, unknown> | null;
  conditions: Array<Record<string, unknown>>;
}

export async function listObjectTypes(token: string): Promise<ObjectType[]> {
  const response = await request("object-type-service", "object-types", {}, token);
  return response.json();
}

export async function createObjectType(
  token: string,
  params: { name: string; appliesTo: "document" | "folder"; attributes: Array<Record<string, unknown>> }
): Promise<ObjectType> {
  const response = await request(
    "object-type-service",
    "object-types",
    jsonInit({ name: params.name, applies_to: params.appliesTo, attributes: params.attributes }),
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
