"use client";

import { useEffect, useState } from "react";
import { lookupUserById } from "@/lib/api";

// Reverse identity resolution for display purposes (post-roadmap Phase 19
// Session 4, ADR 0069; widened in P86-S2). Since P85 (ADR 0240) every
// recorded actor - creators, approvers, claimants, `set_by`, audit `actor` -
// is the gateway-verified principal: a Keycloak user's `sub` (a UUID), or a
// technical account's integer row id (`users-admin` is "2"; ADR 0065).
// This hook turns those ids into usernames:
// - only principal-shaped values (a UUID or digits) are looked up. Older
//   rows still hold a username,
//   service principals (`workflow-service`) and federated actors
//   (`peer:<installation>/<creator>`) are already readable - they are shown
//   as they are;
// - one module-wide cache shared by every component, so ten rows by the same
//   person cost one request, also across panes;
// - a failed lookup (`users.lookup` revoked, account deleted) falls back to
//   the raw id instead of blocking the display.
// Callers show the raw id on hover (`title`), see `principalTitle`.
// Duplicated per app on purpose (ADR 0006: no shared JS between apps).
const PRINCIPAL_PATTERN = /^(?:[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}|\d+)$/i;

const cache = new Map<string, Promise<string>>();

export function isPrincipalId(value: string | null | undefined): value is string {
  return !!value && PRINCIPAL_PATTERN.test(value);
}

// Forgets every resolved name. admin-ui calls it when the active
// installation changes - technical-account ids are small integers, so "2" on
// one installation is a different account on the next. Tests call it too.
export function clearPrincipalNameCache(): void {
  cache.clear();
}

function resolve(token: string, id: string): Promise<string> {
  let pending = cache.get(id);
  if (!pending) {
    pending = lookupUserById(token, id).then(
      (user) => user.username,
      // A failed lookup stays cached as the raw id for this page load: a
      // deleted account will not come back, and retrying on every render
      // would repeat the request for each row.
      () => id
    );
    cache.set(id, pending);
  }
  return pending;
}

export function usePrincipalNames(
  token: string,
  principalIds: (string | null | undefined)[]
): Record<string, string> {
  const [names, setNames] = useState<Record<string, string>>({});
  const key = Array.from(new Set(principalIds.filter(isPrincipalId))).sort().join(",");

  useEffect(() => {
    if (!token || !key) return;
    const unresolved = key.split(",").filter((id) => !(id in names));
    if (unresolved.length === 0) return;
    let cancelled = false;
    Promise.all(unresolved.map(async (id) => [id, await resolve(token, id)] as const)).then(
      (resolved) => {
        if (cancelled) return;
        setNames((prev) => {
          const next = { ...prev };
          for (const [id, name] of resolved) next[id] = name;
          return next;
        });
      }
    );
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token, key]);

  return names;
}

// The display name for one recorded actor: the resolved username, else the
// value as recorded (a legacy username, a service or a federated actor, or a
// UUID that is still loading or could not be resolved).
export function principalName(names: Record<string, string>, value: string | null | undefined): string {
  if (!value) return "";
  return names[value] ?? value;
}

// The hover text that keeps the raw id reachable - only when the name shown
// differs from it.
export function principalTitle(
  names: Record<string, string>,
  value: string | null | undefined
): string | undefined {
  if (!value) return undefined;
  return principalName(names, value) !== value ? value : undefined;
}
