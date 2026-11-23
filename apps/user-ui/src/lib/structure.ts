"use client";

import { useEffect, useState } from "react";
import { getAllowedChildren, getFolder, type AllowedChildren } from "@/lib/api";

// Filing structure in the user UI (P91-S3, ADR 0255): what may be created
// in the folder the user is looking at. `undefined` while loading, `null`
// when the rules can't be read - forms then fall back to everything and
// leave the decision to the server, which checks either way.
export function useAllowedChildren(
  token: string | null | undefined,
  folderId: string
): AllowedChildren | null | undefined {
  const [allowed, setAllowed] = useState<AllowedChildren | null | undefined>(undefined);
  useEffect(() => {
    if (!token) return;
    let cancelled = false;
    setAllowed(undefined);
    (async () => {
      try {
        const parent =
          folderId === "root"
            ? ({ root: true } as const)
            : { objectTypeId: (await getFolder(token, folderId)).object_type_id };
        const result = await getAllowedChildren(token, parent);
        if (!cancelled) setAllowed(result);
      } catch {
        if (!cancelled) setAllowed(null);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [token, folderId]);
  return allowed;
}

// Filters a list of types to the allowed ones; without rules, all of them.
export function allowedSubset<T extends { name: string }>(
  types: T[],
  allowedNames: { name: string }[] | undefined
): T[] {
  if (!allowedNames) return types;
  const names = new Set(allowedNames.map((t) => t.name));
  return types.filter((t) => names.has(t.name));
}
