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
import { useInstallation } from "./installation-context";

// Bekannte Vereinfachung dieses Grundgerüsts: Tokens liegen im localStorage,
// nicht in einem httpOnly-Cookie - einfachste Variante für eine rein
// clientseitig gerenderte SPA ohne eigenes Backend (Konzept 8). XSS-Härtung
// (z. B. über eine Session-Cookie-Ausgabe durch das Gateway) ist ein späterer
// Schritt, siehe docs/services/user-ui.md "Offene Punkte".
interface StoredTokens {
  accessToken: string;
  refreshToken: string;
  expiresAt: number;
}

// Eigener Storage-Key je Installation (P4-S5, Konzept 3a/8): Wechsel zwischen
// Installationen darf die Sitzung(en) der jeweils anderen nicht berühren -
// "kein erneutes Anmelden, solange die Sitzung gilt" gilt pro Installation,
// nicht global.
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
    // 30s Sicherheitsabstand, damit ein Refresh nicht erst nach Ablauf greift.
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
  const { activeInstallation } = useInstallation();
  const installationId = activeInstallation.id;

  const [tokens, setTokens] = useState<StoredTokens | null>(null);
  const [user, setUser] = useState<CurrentUser | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const refreshTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const clearSession = useCallback(() => {
    if (refreshTimer.current) clearTimeout(refreshTimer.current);
    setTokens(null);
    setUser(null);
    saveStoredTokens(installationId, null);
  }, [installationId]);

  const applySession = useCallback(
    async (response: TokenResponse) => {
      const stored = toStoredTokens(response);
      saveStoredTokens(installationId, stored);
      setTokens(stored);
      const me = await getCurrentUser(stored.accessToken);
      setUser(me);
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

  // Proaktiver Refresh statt reaktiv auf 401 zu warten - einfacher für dieses
  // Grundgerüst, da nur ein einziger, vorhersagbarer Ablaufzeitpunkt je
  // Session existiert (kein Multi-Tab-Koordinierungsbedarf berücksichtigt).
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

  // Läuft bei jedem Wechsel der aktiven Installation erneut: lädt deren
  // eigene gespeicherte Sitzung (falls vorhanden und noch gültig), ohne die
  // gespeicherten Sitzungen anderer Installationen anzufassen.
  useEffect(() => {
    setIsLoading(true);
    const stored = loadStoredTokens(installationId);
    if (!stored || stored.expiresAt <= Date.now()) {
      setTokens(null);
      setUser(null);
      setIsLoading(false);
      return;
    }
    setTokens(stored);
    getCurrentUser(stored.accessToken)
      .then(setUser)
      .catch(() => clearSession())
      .finally(() => setIsLoading(false));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [installationId]);

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
