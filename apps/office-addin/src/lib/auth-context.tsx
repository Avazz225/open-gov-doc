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
import { ApiError, getCurrentUser, login as apiLogin, refreshToken as apiRefresh } from "./api";
import type { CurrentUser, TokenResponse } from "./api";

const STORAGE_KEY = "ogdoc.tokens";

// Known simplification of this scaffolding: tokens live in localStorage,
// not in an httpOnly cookie (identical pattern to user-ui/reviewer-ui,
// ADR 0006). In the Office taskpane context the same limitation applies, with an
// additional, documented nuance: the storage location/lifetime of the
// taskpane webview differs per Office version/platform (see
// docs/services/office-addin.md "Open Points") - having to log in again
// after restarting Word is therefore an expected case, not a bug.
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
    expiresAt: Date.now() + (response.expires_in - 30) * 1000,
  };
}

interface AuthContextValue {
  user: CurrentUser | null;
  accessToken: string | null;
  isLoading: boolean;
  login: (username: string, password: string) => Promise<void>;
  logout: () => void;
}

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [tokens, setTokens] = useState<StoredTokens | null>(null);
  const [user, setUser] = useState<CurrentUser | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const refreshTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const clearSession = useCallback(() => {
    if (refreshTimer.current) clearTimeout(refreshTimer.current);
    setTokens(null);
    setUser(null);
    saveStoredTokens(null);
  }, []);

  const applySession = useCallback(async (response: TokenResponse) => {
    const stored = toStoredTokens(response);
    saveStoredTokens(stored);
    setTokens(stored);
    setUser(await getCurrentUser(stored.accessToken));
  }, []);

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
      .then(setUser)
      .catch(() => clearSession())
      .finally(() => setIsLoading(false));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const value: AuthContextValue = {
    user,
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
