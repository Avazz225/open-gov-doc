import { revokeRoleForRun } from "./role-grant";

export default async function globalTeardown() {
  await revokeRoleForRun("process-designer");
}
