"use client";

import { useEffect, useState } from "react";
import { lookupUserById } from "@/lib/api";

// Reverse identity resolution for display purposes (post-roadmap Phase 19
// Session 4, ADR 0069) - resolves a list of raw principal_id UUIDs
// (delegations, teamspace members) into usernames, with a simple in-memory
// cache across the hook's calls (no repeat request for already-resolved
// IDs). Falls back to the raw UUID on failure (e.g. `users.lookup` revoked
// from the "everyone" group, or an account deleted in the meantime) instead
// of blocking the display.
export function usePrincipalNames(token: string, principalIds: string[]): Record<string, string> {
  const [names, setNames] = useState<Record<string, string>>({});
  const key = Array.from(new Set(principalIds)).sort().join(",");

  useEffect(() => {
    if (!token || !key) return;
    const unresolved = key.split(",").filter((id) => !(id in names));
    if (unresolved.length === 0) return;
    let cancelled = false;
    Promise.all(
      unresolved.map(async (id) => {
        try {
          const user = await lookupUserById(token, id);
          return [id, user.username] as const;
        } catch {
          return [id, id] as const;
        }
      })
    ).then((resolved) => {
      if (cancelled) return;
      setNames((prev) => {
        const next = { ...prev };
        for (const [id, name] of resolved) next[id] = name;
        return next;
      });
    });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token, key]);

  return names;
}
