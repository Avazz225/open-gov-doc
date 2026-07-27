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

// Multi-Installation-Grundlage der Admin-UI (P4-S5, Konzept 3a/8): eine
// Liste konfigurierter Installationen (je eigener Gateway-Endpunkt) plus die
// aktuell "aktive" - `AuthProvider` hält davon abhängig eine eigene Sitzung
// je Installation (siehe dort), kein Single-Sign-on über Installationsgrenzen
// hinweg (bewusste Isolation, Konzept 3a).
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

  // Synchron im Render statt in einem `useEffect` gesetzt: `AuthProvider` ist
  // ein Kind dieser Komponente und braucht die aktuelle Gateway-Adresse
  // schon in seinem eigenen ersten Render-Effekt (Sitzungswiederherstellung
  // für die aktive Installation). Ein `useEffect` hier würde erst NACH dem
  // Kind-Effekt feuern (React führt Effekte von unten nach oben aus) und die
  // erste Anfrage nach einem Installationswechsel noch gegen die alte
  // Adresse schicken. Reine Zuweisung einer externen Modulvariablen (kein
  // DOM-/Subscription-Seiteneffekt), bei wiederholter Ausführung mit
  // demselben Wert idempotent - unter React Strict Mode unkritisch.
  setGatewayBaseUrl(activeInstallation.gatewayBaseUrl);

  const switchInstallation = useCallback((id: string) => {
    setActiveInstallationId(id);
    saveActiveInstallationId(id);
  }, []);

  // Falls die aktive Installation entfernt wurde, auf die erste verbleibende
  // wechseln, statt in einem inkonsistenten Zustand zu bleiben.
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
      if (prev.length <= 1) return prev; // mindestens eine Installation bleibt immer erhalten
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
