import { GATEWAY_BASE_URL } from "./config";

// Multiple installations (P4-S5, Concept 3a/8): The admin UI keeps a list
// of configured installations (each with its own gateway endpoint) purely
// client-side in `localStorage` - no dedicated backend process for this, since
// this list is purely an administrative UI preference, not business data.
export interface Installation {
  id: string;
  name: string;
  gatewayBaseUrl: string;
}

const INSTALLATIONS_KEY = "dms.installations";
const ACTIVE_INSTALLATION_KEY = "dms.activeInstallationId";
const DEFAULT_INSTALLATION_ID = "default";

function defaultInstallation(): Installation {
  return { id: DEFAULT_INSTALLATION_ID, name: "Lokal", gatewayBaseUrl: GATEWAY_BASE_URL };
}

export function loadInstallations(): Installation[] {
  if (typeof window === "undefined") return [defaultInstallation()];
  const raw = window.localStorage.getItem(INSTALLATIONS_KEY);
  if (!raw) return [defaultInstallation()];
  try {
    const parsed = JSON.parse(raw) as Installation[];
    return Array.isArray(parsed) && parsed.length > 0 ? parsed : [defaultInstallation()];
  } catch {
    return [defaultInstallation()];
  }
}

export function saveInstallations(installations: Installation[]): void {
  if (typeof window === "undefined") return;
  window.localStorage.setItem(INSTALLATIONS_KEY, JSON.stringify(installations));
}

export function loadActiveInstallationId(): string | null {
  if (typeof window === "undefined") return null;
  return window.localStorage.getItem(ACTIVE_INSTALLATION_KEY);
}

export function saveActiveInstallationId(id: string): void {
  if (typeof window === "undefined") return;
  window.localStorage.setItem(ACTIVE_INSTALLATION_KEY, id);
}
