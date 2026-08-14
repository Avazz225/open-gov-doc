"use client";

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from "react";
import {
  ApiError,
  getCurrentUser,
  getEffectivePermissions,
  login as apiLogin,
  refreshToken as apiRefresh,
} from "./api";
import type { CurrentUser, TokenResponse } from "./api";
import { useInstallation } from "./installation-context";

// Known simplification of this scaffolding: tokens live in localStorage,
// not in an httpOnly cookie - the simplest option for a purely client-side
// rendered SPA without its own backend (Concept 8). XSS hardening (e.g. via
// a session cookie issued by the gateway) is a later step, see
// docs/services/user-ui.md "Open Points".
interface StoredTokens {
  accessToken: string;
  refreshToken: string;
  expiresAt: number;
}

// Separate storage key per installation (P4-S5, Concept 3a/8): switching
// between installations must not touch the other installations' session(s) -
// "no need to log in again while the session is valid" applies per
// installation, not globally.
function storageKey(installationId: string): string {
  return `dms.tokens.${installationId}`;
}

function loadStoredTokens(installationId: string): StoredTokens | null {
  if (typeof window === "undefined") return null;
  const raw = window.localStorage.getItem(storageKey(installationId));
  if (!raw) return null;
  try {
    return JSON.parse(raw) as StoredTokens;
  } catch {
    return null;
  }
}

function saveStoredTokens(installationId: string, tokens: StoredTokens | null): void {
  if (typeof window === "undefined") return;
  if (tokens === null) {
    window.localStorage.removeItem(storageKey(installationId));
  } else {
    window.localStorage.setItem(storageKey(installationId), JSON.stringify(tokens));
  }
}

function toStoredTokens(response: TokenResponse): StoredTokens {
  return {
    accessToken: response.access_token,
    refreshToken: response.refresh_token,
    // 30s safety margin so a refresh doesn't only kick in after expiry.
    expiresAt: Date.now() + (response.expires_in - 30) * 1000,
  };
}

interface AuthContextValue {
  user: CurrentUser | null;
  // Domain-separated admin roles (4.6, P6-S5): system-native capabilities
  // from the Permission Service, NOT from `user.realm_roles` - a separate
  // source, see api.ts `getEffectivePermissions`.
  permissions: string[];
  accessToken: string | null;
  isLoading: boolean;
  login: (username: string, password: string) => Promise<void>;
  logout: () => void;
}

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const { activeInstallation } = useInstallation();
  const installationId = activeInstallation.id;

  const [tokens, setTokens] = useState<StoredTokens | null>(null);
  const [user, setUser] = useState<CurrentUser | null>(null);
  const [permissions, setPermissions] = useState<string[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const refreshTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const clearSession = useCallback(() => {
    if (refreshTimer.current) clearTimeout(refreshTimer.current);
    setTokens(null);
    setUser(null);
    setPermissions([]);
    saveStoredTokens(installationId, null);
  }, [installationId]);

  const applySession = useCallback(
    async (response: TokenResponse) => {
      const stored = toStoredTokens(response);
      saveStoredTokens(installationId, stored);
      setTokens(stored);
      const me = await getCurrentUser(stored.accessToken);
      setUser(me);
      setPermissions(await getEffectivePermissions(stored.accessToken, me.sub));
    },
    [installationId]
  );

  const login = useCallback(
    async (username: string, password: string) => {
      const response = await apiLogin(username, password);
      await applySession(response);
    },
    [applySession]
  );

  const logout = useCallback(() => {
    clearSession();
  }, [clearSession]);

  // Proactive refresh instead of reacting to a 401 - simpler for this
  // scaffolding, since only a single, predictable expiry time exists per
  // session (no multi-tab coordination need is accounted for).
  useEffect(() => {
    if (!tokens) return;
    const delay = Math.max(tokens.expiresAt - Date.now(), 0);
    refreshTimer.current = setTimeout(async () => {
      try {
        const response = await apiRefresh(tokens.refreshToken);
        await applySession(response);
      } catch {
        clearSession();
      }
    }, delay);
    return () => {
      if (refreshTimer.current) clearTimeout(refreshTimer.current);
    };
  }, [tokens, applySession, clearSession]);

  // Reruns on every switch of the active installation: loads its own stored
  // session (if present and still valid), without touching the stored
  // sessions of other installations.
  useEffect(() => {
    setIsLoading(true);
    const stored = loadStoredTokens(installationId);
    if (!stored || stored.expiresAt <= Date.now()) {
      setTokens(null);
      setUser(null);
      setPermissions([]);
      setIsLoading(false);
      return;
    }
    setTokens(stored);
    getCurrentUser(stored.accessToken)
      .then(async (me) => {
        setUser(me);
        setPermissions(await getEffectivePermissions(stored.accessToken, me.sub));
      })
      .catch(() => clearSession())
      .finally(() => setIsLoading(false));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [installationId]);

  const value: AuthContextValue = {
    user,
    permissions,
    accessToken: tokens?.accessToken ?? null,
    isLoading,
    login,
    logout,
  };

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const context = useContext(AuthContext);
  if (!context) throw new Error("useAuth muss innerhalb von <AuthProvider> verwendet werden");
  return context;
}

export { ApiError };
