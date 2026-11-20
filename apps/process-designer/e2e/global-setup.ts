import { grantRoleForRun } from "./role-grant";

// Saving/deleting process and DMN definitions needs `admin.object_config`
// (role domain-admin-config, looked up by permission, never by id).
export default async function globalSetup() {
  await grantRoleForRun("process-designer", (role) => role.permissions.includes("admin.object_config"));
}
