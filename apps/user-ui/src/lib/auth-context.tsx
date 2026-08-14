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
  logoutSession,
  refreshToken as apiRefresh,
} from "./api";
import type { CurrentUser, TokenResponse } from "./api";

const STORAGE_KEY = "dms.tokens";

// Known simplification of this scaffold: tokens live in localStorage, not
// an httpOnly cookie - the simplest option for a purely client-rendered SPA
// without its own backend (concept 8). XSS hardening (e.g. via a session
// cookie issued by the gateway) is a later step, see
// docs/services/user-ui.md "Open Points".
interface StoredTokens {
  accessToken: string;
  refreshToken: string;
  expiresAt: number;
}

function loadStoredTokens(): StoredTokens | null {
  if (typeof window === "undefined") return null;
  const raw = window.localStorage.getItem(STORAGE_KEY);
  if (!raw) return null;
  try {
    return JSON.parse(raw) as StoredTokens;
  } catch {
    return null;
  }
}

function saveStoredTokens(tokens: StoredTokens | null): void {
  if (typeof window === "undefined") return;
  if (tokens === null) {
    window.localStorage.removeItem(STORAGE_KEY);
  } else {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(tokens));
  }
}

function toStoredTokens(response: TokenResponse): StoredTokens {
  return {
    accessToken: response.access_token,
    refreshToken: response.refresh_token,
    // 30s safety margin so a refresh doesn't kick in only after expiry.
    expiresAt: Date.now() + (response.expires_in - 30) * 1000,
  };
}

interface AuthContextValue {
  user: CurrentUser | null;
  // Domain-separated admin roles (4.6): system-native capabilities from the
  // Permission Service, NOT from `user.realm_roles` - separate source, same
  // pattern as `apps/admin-ui`'s auth context (see api.ts
  // `getEffectivePermissions`). First consumer since post-roadmap Phase 19
  // Session 10 (ADR 0075): `admin.legal_hold`.
  permissions: string[];
  accessToken: string | null;
  isLoading: boolean;
  login: (username: string, password: string) => Promise<void>;
  // SSO/automatic login (post-roadmap feature): `login/callback/page.tsx`
  // uses this to adopt a session obtained via `POST /oidc/callback` through
  // the same storage path as a regular form login, instead of duplicating
  // it.
  applySession: (response: TokenResponse) => Promise<void>;
  logout: () => void;
}

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
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
    saveStoredTokens(null);
  }, []);

  const applySession = useCallback(
    async (response: TokenResponse) => {
      const stored = toStoredTokens(response);
      saveStoredTokens(stored);
      setTokens(stored);
      const me = await getCurrentUser(stored.accessToken);
      setUser(me);
      setPermissions(await getEffectivePermissions(stored.accessToken, me.sub));
    },
    []
  );

  const login = useCallback(
    async (username: string, password: string) => {
      const response = await apiLogin(username, password);
      await applySession(response);
    },
    [applySession]
  );

  const logout = useCallback(() => {
    // SSO/automatic login (post-roadmap feature): also actually ends the
    // session on the Keycloak side (best-effort, does not block the local
    // logout on a network error) - without this, a SPNEGO-capable browser
    // would immediately log back in automatically on the next visit, since
    // Keycloak's own SSO session would remain untouched.
    if (tokens) {
      logoutSession(tokens.refreshToken).catch(() => {
        // Deliberately ignored - see comment above.
      });
    }
    clearSession();
  }, [clearSession, tokens]);

  // Proactive refresh instead of reactively waiting for a 401 - simpler for
  // this scaffold, since there is only a single, predictable expiry point
  // per session (no multi-tab coordination need considered).
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

  useEffect(() => {
    const stored = loadStoredTokens();
    if (!stored || stored.expiresAt <= Date.now()) {
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
  }, []);

  const value: AuthContextValue = {
    user,
    permissions,
    accessToken: tokens?.accessToken ?? null,
    isLoading,
    login,
    applySession,
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
