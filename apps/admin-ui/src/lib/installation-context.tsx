"use client";

import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from "react";
import { setGatewayBaseUrl } from "./api";
import {
  type Installation,
  loadActiveInstallationId,
  loadInstallations,
  saveActiveInstallationId,
  saveInstallations,
} from "./installations";

interface InstallationContextValue {
  installations: Installation[];
  activeInstallation: Installation;
  addInstallation: (params: { name: string; gatewayBaseUrl: string }) => void;
  removeInstallation: (id: string) => void;
  switchInstallation: (id: string) => void;
}

const InstallationContext = createContext<InstallationContextValue | null>(null);

// Multi-installation foundation of the admin UI (P4-S5, Concept 3a/8): a
// list of configured installations (each with its own gateway endpoint) plus the
// currently "active" one - `AuthProvider` maintains its own session
// per installation depending on this (see there), no single sign-on across
// installation boundaries (deliberate isolation, Concept 3a).
export function InstallationProvider({ children }: { children: ReactNode }) {
  const [installations, setInstallations] = useState<Installation[]>(() => loadInstallations());
  const [activeInstallationId, setActiveInstallationId] = useState<string>(() => {
    const stored = loadActiveInstallationId();
    const list = loadInstallations();
    return stored && list.some((installation) => installation.id === stored)
      ? stored
      : list[0].id;
  });

  const activeInstallation =
    installations.find((installation) => installation.id === activeInstallationId) ??
    installations[0];

  // Set synchronously during render instead of in a `useEffect`: `AuthProvider`
  // is a child of this component and needs the current gateway address
  // already in its own first render effect (session restoration
  // for the active installation). A `useEffect` here would only fire AFTER the
  // child effect (React runs effects bottom-up) and would send the
  // first request after an installation switch against the old
  // address. Plain assignment of an external module variable (no
  // DOM/subscription side effect), idempotent when run repeatedly with
  // the same value - uncritical under React Strict Mode.
  setGatewayBaseUrl(activeInstallation.gatewayBaseUrl);

  const switchInstallation = useCallback((id: string) => {
    setActiveInstallationId(id);
    saveActiveInstallationId(id);
  }, []);

  // If the active installation was removed, switch to the first remaining
  // one instead of staying in an inconsistent state.
  useEffect(() => {
    if (!installations.some((installation) => installation.id === activeInstallationId)) {
      switchInstallation(installations[0].id);
    }
  }, [installations, activeInstallationId, switchInstallation]);

  const addInstallation = useCallback((params: { name: string; gatewayBaseUrl: string }) => {
    setInstallations((prev) => {
      const next = [
        ...prev,
        { id: crypto.randomUUID(), name: params.name, gatewayBaseUrl: params.gatewayBaseUrl },
      ];
      saveInstallations(next);
      return next;
    });
  }, []);

  const removeInstallation = useCallback((id: string) => {
    setInstallations((prev) => {
      if (prev.length <= 1) return prev; // at least one installation is always kept
      const next = prev.filter((installation) => installation.id !== id);
      saveInstallations(next);
      return next;
    });
  }, []);

  return (
    <InstallationContext.Provider
      value={{ installations, activeInstallation, addInstallation, removeInstallation, switchInstallation }}
    >
      {children}
    </InstallationContext.Provider>
  );
}

export function useInstallation(): InstallationContextValue {
  const context = useContext(InstallationContext);
  if (!context) {
    throw new Error("useInstallation muss innerhalb von <InstallationProvider> verwendet werden");
  }
  return context;
}
