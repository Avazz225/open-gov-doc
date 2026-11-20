import { readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

// One role grant per test RUN, not per test (P84-S1). Grants are idempotent
// in permission-service (same principal/role/resource returns the same
// assignment), so per-test grant/revoke fixtures shared ONE assignment across
// parallel workers and the first worker's teardown revoked it under the
// others. Playwright runs globalSetup/globalTeardown once per run regardless
// of the worker count, so the grant now lives there. An assignment the test
// account already held before the run is never revoked.

const USERNAME = process.env.E2E_USERNAME || "users-admin";
const PASSWORD = process.env.E2E_PASSWORD || "users-admin";
const GATEWAY = process.env.E2E_GATEWAY_BASE_URL || "http://localhost:8009";

type Role = { id: number; name: string; permissions: string[] };
type Assignment = { id: number; principal_id: string; role_id: number; resource_id: string };
type State = { assignmentId: number; preexisting: boolean };

function stateFile(app: string): string {
  return join(tmpdir(), `dms-e2e-${app}-role-grant.json`);
}

async function token(): Promise<string> {
  const response = await fetch(`${GATEWAY}/api/auth-service/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username: USERNAME, password: PASSWORD }),
  });
  if (!response.ok) throw new Error(`API login failed: ${response.status}`);
  return ((await response.json()) as { access_token: string }).access_token;
}

async function getJson<T>(path: string, auth: Record<string, string>): Promise<T> {
  const response = await fetch(`${GATEWAY}${path}`, { headers: auth });
  if (!response.ok) throw new Error(`GET ${path} failed: ${response.status}`);
  return (await response.json()) as T;
}

export async function grantRoleForRun(app: string, matches: (role: Role) => boolean): Promise<void> {
  const auth = { Authorization: `Bearer ${await token()}` };
  const me = await getJson<{ sub: string }>("/api/auth-service/me", auth);
  const role = (await getJson<Role[]>("/api/permission-service/roles", auth)).find(matches);
  if (!role) throw new Error("No matching role seeded for the e2e grant");

  const held = await getJson<Assignment[]>(
    `/api/permission-service/role-assignments?principal_id=${encodeURIComponent(me.sub)}`,
    auth
  );
  const existing = held.find((a) => a.role_id === role.id && a.resource_id === "root");
  if (existing) {
    writeFileSync(stateFile(app), JSON.stringify({ assignmentId: existing.id, preexisting: true }));
    return;
  }
  const response = await fetch(`${GATEWAY}/api/permission-service/role-assignments`, {
    method: "POST",
    headers: { ...auth, "Content-Type": "application/json" },
    body: JSON.stringify({ principal_type: "user", principal_id: me.sub, role_id: role.id, resource_id: "root" }),
  });
  if (!response.ok) throw new Error(`Role grant failed: ${response.status}`);
  const body = (await response.json()) as { role_assignment: { id: number } | null };
  if (!body.role_assignment) throw new Error("Role grant went to approval instead of being applied");
  const state: State = { assignmentId: body.role_assignment.id, preexisting: false };
  writeFileSync(stateFile(app), JSON.stringify(state));
}

export async function revokeRoleForRun(app: string): Promise<void> {
  let state: State;
  try {
    state = JSON.parse(readFileSync(stateFile(app), "utf8")) as State;
  } catch {
    return; // setup never got as far as granting
  }
  rmSync(stateFile(app), { force: true });
  if (state.preexisting) return;
  const auth = { Authorization: `Bearer ${await token()}` };
  await fetch(`${GATEWAY}/api/permission-service/role-assignments/${state.assignmentId}`, {
    method: "DELETE",
    headers: auth,
  });
}
