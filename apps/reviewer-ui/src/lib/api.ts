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

// Every call goes through the gateway (3.5): /api/{service_type}/{path}
// instead of direct backend addresses - registry resolution and auth
// checking happen there, not here.
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

// Unlike admin-ui/process-designer, this app does not evaluate a
// domain-separated admin role (neither task completion nor four-eyes
// decisions are capability-gated on the backend, see
// docs/services/reviewer-ui.md "Authorization") - still fetched in case a
// later session wants to restrict individual actions in a targeted way
// without having to touch the auth layer (identical pattern to
// process-designer/admin-ui/user-ui).
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

export interface MaintenanceMode {
  active: boolean;
}

export async function getMaintenanceStatus(token: string): Promise<MaintenanceMode> {
  const response = await request("permission-service", "maintenance-mode", {}, token);
  return response.json();
}

// --- Workflow Engine (7.1) - approval tasks (Manual/Signature Tasks) -------
//
// `GET /tasks` (workflow-service, since P14-S2) lists the ready tasks across
// ALL running instances - previously there was only the instance-bound
// `GET /instances/{id}/tasks`, which is unsuitable for a reviewer inbox.
// Federated tasks (7.4) are already filtered out server-side (they are
// completed automatically via the Federation Hub, never by a human).
export interface ReadyTaskWithInstance {
  id: string;
  name: string;
  lane: string | null;
  data: Record<string, unknown>;
  extensions: Record<string, string>;
  // Task-claim mechanism (post-roadmap phase 31 session 10) - `null` if unclaimed.
  claimed_by: string | null;
  grant_kind: string | null;
  instance_id: string;
  process_definition_id: number;
  business_key: string | null;
}

export async function listReadyTasks(token: string): Promise<ReadyTaskWithInstance[]> {
  const response = await request("workflow-service", "tasks", {}, token);
  return response.json();
}

// Authenticated direct links / "Vorgang" detail view (post-roadmap phase
// 29, ADR 0109/0110) - the first UI anywhere that shows a single process
// instance by ID. Deliberately no task history (workflow-service persists
// none, see ADR 0019/0107) - only current status and currently-open tasks.
export interface ProcessInstance {
  id: string;
  process_definition_id: number;
  business_key: string | null;
  status: "running" | "completed";
  created_by: string;
  created_at: string;
  updated_at: string;
  completed_at: string | null;
}

export async function getInstance(token: string, instanceId: string): Promise<ProcessInstance> {
  const response = await request(
    "workflow-service",
    `instances/${encodeURIComponent(instanceId)}`,
    {},
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
  claimed_by: string | null;
  grant_kind: string | null;
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
  params: {
    instanceId: string;
    taskId: string;
    completedBy: string;
    data?: Record<string, unknown>;
    signatureId?: string;
    onBehalfOfPrincipalId?: string;
  }
): Promise<void> {
  await request(
    "workflow-service",
    `instances/${params.instanceId}/tasks/${params.taskId}/complete`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        completed_by: params.completedBy,
        data: params.data ?? {},
        signature_id: params.signatureId ?? null,
        on_behalf_of_principal_id: params.onBehalfOfPrincipalId ?? null,
      }),
    },
    token
  );
}

// Task-claim mechanism & dynamic org-hierarchy access grants (14.2,
// post-roadmap phase 31 session 10) - distinct from and additional to the
// self-service delegation mechanism below (ADR 0048): that one is a person
// naming ONE deputy for themselves, this one is auto-resolved from
// permission-service's org-hierarchy data and can grant SEVERAL people at
// once, but requires an existing claim to resolve "the assignee" from.
export async function claimTask(
  token: string,
  params: { instanceId: string; taskId: string; principalId: string }
): Promise<void> {
  await request(
    "workflow-service",
    `instances/${params.instanceId}/tasks/${params.taskId}/claim`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ principal_id: params.principalId }),
    },
    token
  );
}

export async function releaseTaskClaim(
  token: string,
  params: { instanceId: string; taskId: string }
): Promise<void> {
  await request(
    "workflow-service",
    `instances/${params.instanceId}/tasks/${params.taskId}/claim`,
    { method: "DELETE" },
    token
  );
}

export interface OrgHierarchyGrantResult {
  grant_kind: string;
  deputy_principal_ids: string[];
}

export async function createTaskOrgHierarchyGrant(
  token: string,
  params: {
    instanceId: string;
    taskId: string;
    grantKind: "supervisor" | "supervisor_chain" | "org_unit";
    orgUnitOf?: "assignee" | "creator";
  }
): Promise<OrgHierarchyGrantResult> {
  const response = await request(
    "workflow-service",
    `instances/${params.instanceId}/tasks/${params.taskId}/org-hierarchy-grant`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        grant_kind: params.grantKind,
        org_unit_of: params.orgUnitOf ?? null,
      }),
    },
    token
  );
  return response.json();
}

// Absence deputization (4.4a, P14-S11) - who the logged-in person is
// currently actively registered as a deputy for, populated by the new
// `permission-service` delegation endpoint (see
// docs/services/permission-service.md). Only this list populates the "On
// behalf of" selector in `TaskList.tsx` - the actual permission check
// happens server-side at completion itself (workflow-service), this list is
// purely a UX aid.
export interface Delegation {
  id: string;
  delegator_principal_id: string;
  deputy_principal_id: string;
  starts_at: string;
  ends_at: string;
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

// Org-hierarchy foundation (post-roadmap phase 31 session 9) - direct
// reports of the logged-in person, the basis for the supervisor/team task
// oversight view (session 10... actually 11, see TeamTaskList.tsx). Queried
// by `user.sub`, not `user.username` - unlike the claim/completion free-text
// fields elsewhere in this app, `SupervisorAssignment.supervisor_principal_id`
// must be the real Keycloak `sub` for the org-hierarchy access grant
// (session 10) to actually be exercisable (workflow-service's on-behalf-of
// check compares a delegation's deputy against the real `X-DMS-Principal`,
// which is always `sub` - see gateway-service/main.py), so this view reuses
// exactly that identity for consistency.
export interface SupervisorAssignment {
  id: number;
  principal_id: string;
  supervisor_principal_id: string;
  created_at: string;
}

export async function listDirectReports(
  token: string,
  supervisorPrincipalId: string
): Promise<SupervisorAssignment[]> {
  const response = await request(
    "permission-service",
    `supervisor-assignments?supervisor_principal_id=${encodeURIComponent(supervisorPrincipalId)}`,
    {},
    token
  );
  return response.json();
}

// --- Permission Service (4.3) - generic four-eyes approvals ----------------
//
// `permission-service`'s `/approval-requests` is already fully generic
// across ALL `action_type`s (document/folder deletion, superuser
// activation, scope locks, migration transfers, ...) - previously there
// were only narrowly filtered per-caller consumers (admin-ui/user-ui, see
// docs/services/reviewer-ui.md); this app is the first generic consumer
// that shows all action types equally.
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
  params?: { status?: string }
): Promise<ApprovalRequest[]> {
  const query = params?.status ? `?status=${encodeURIComponent(params.status)}` : "";
  const response = await request("permission-service", `approval-requests${query}`, {}, token);
  return response.json();
}

export async function approveRequest(
  token: string,
  requestId: string,
  approvedBy: string
): Promise<ApprovalRequest> {
  const response = await request(
    "permission-service",
    `approval-requests/${requestId}/approve`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ approved_by: approvedBy }),
    },
    token
  );
  return response.json();
}

export async function rejectRequest(
  token: string,
  requestId: string,
  params: { rejectedBy: string; reason?: string }
): Promise<ApprovalRequest> {
  const response = await request(
    "permission-service",
    `approval-requests/${requestId}/reject`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ rejected_by: params.rejectedBy, reason: params.reason ?? null }),
    },
    token
  );
  return response.json();
}
