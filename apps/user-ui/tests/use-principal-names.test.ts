import { describe, expect, it } from "vitest";
import { isPrincipalId, principalName, principalTitle } from "@/lib/usePrincipalNames";

// P86-S2: which recorded actors are looked up, and how a resolved or
// unresolved one is shown.
describe("usePrincipalNames helpers", () => {
  it("treats Keycloak UUIDs and technical-account integer ids as principals", () => {
    expect(isPrincipalId("4df644a9-e418-4287-9b42-754544c4c75a")).toBe(true);
    expect(isPrincipalId("2")).toBe(true);
  });

  it("leaves legacy usernames, services and federated actors as they are", () => {
    expect(isPrincipalId("alice")).toBe(false);
    expect(isPrincipalId("workflow-service")).toBe(false);
    expect(isPrincipalId("peer:berlin/alice")).toBe(false);
    expect(isPrincipalId("")).toBe(false);
    expect(isPrincipalId(null)).toBe(false);
  });

  it("shows the resolved name with the raw id on hover, else the value as recorded", () => {
    const names = { "2": "users-admin" };
    expect(principalName(names, "2")).toBe("users-admin");
    expect(principalTitle(names, "2")).toBe("2");
    expect(principalName(names, "alice")).toBe("alice");
    expect(principalTitle(names, "alice")).toBeUndefined();
    expect(principalName(names, null)).toBe("");
  });
});
