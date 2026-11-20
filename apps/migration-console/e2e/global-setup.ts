import { grantRoleForRun } from "./role-grant";

// Pairing installations needs `admin.migration_management`
// (role domain-admin-migration, P59-S5).
export default async function globalSetup() {
  await grantRoleForRun("migration-console", (role) => role.name === "domain-admin-migration");
}
