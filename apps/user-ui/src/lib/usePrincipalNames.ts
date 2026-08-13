"use client";

import { useEffect, useState } from "react";
import { lookupUserById } from "@/lib/api";

// Rückwärts-Identitätsauflösung für Anzeigezwecke (Post-Roadmap Phase 19
// Session 4, ADR 0069) - löst eine Liste roher principal_id-UUIDs
// (Delegationen, Teamspace-Mitglieder) in Nutzernamen auf, mit einem
// einfachen In-Memory-Cache über den Hook-Aufruf hinweg (kein erneuter
// Request für bereits aufgelöste IDs). Fällt bei einem Fehlschlag (z. B.
// `users.lookup` der "everyone"-Gruppe entzogen, oder ein zwischenzeitlich
// gelöschtes Konto) auf die rohe UUID zurück, statt die Anzeige zu
// blockieren.
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
