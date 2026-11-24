"use client";

import { useEffect, useState } from "react";
import {
  getLicenseComponents,
  getLicenseDisplayConfig,
  type LicensableComponent,
} from "@/lib/api";

// What a licence extension would add (P91-S6): the licensable components
// that aren't fully licensed, and whether the installation wants such hints
// at all. The sidebar asks on every page, so one answer is shared for a
// minute; `invalidateLicenseHints` drops it after the switch is changed or a
// licence installed, and makes every mounted hook reload right away.

const CACHE_MS = 60_000;
let cached: { at: number; value: Promise<LicenseHints> } | null = null;
const listeners = new Set<() => void>();

export interface LicenseHints {
  showHints: boolean;
  components: LicensableComponent[];
}

export function invalidateLicenseHints(): void {
  cached = null;
  listeners.forEach((reload) => reload());
}

function load(token: string): Promise<LicenseHints> {
  if (cached && Date.now() - cached.at < CACHE_MS) return cached.value;
  const value = Promise.all([getLicenseComponents(token), getLicenseDisplayConfig(token)])
    .then(([components, config]) => ({ components, showHints: config.show_upgrade_hints }))
    .catch(() => {
      cached = null;
      return { components: [], showHints: false };
    });
  cached = { at: Date.now(), value };
  return value;
}

export function useLicenseHints(token: string | null | undefined): LicenseHints | null {
  const [hints, setHints] = useState<LicenseHints | null>(null);
  useEffect(() => {
    if (!token) return;
    let active = true;
    const reload = () => {
      load(token).then((value) => {
        if (active) setHints(value);
      });
    };
    reload();
    listeners.add(reload);
    return () => {
      active = false;
      listeners.delete(reload);
    };
  }, [token]);
  return hints;
}

export function notFullyLicensed(components: LicensableComponent[]): LicensableComponent[] {
  return components.filter((c) => c.status !== "licensed");
}
