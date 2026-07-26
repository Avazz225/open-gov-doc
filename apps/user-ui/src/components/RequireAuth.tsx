"use client";

import { useRouter } from "next/navigation";
import { useEffect, type ReactNode } from "react";
import { useAuth } from "@/lib/auth-context";

// Statischer Export hat keinen Server, der Redirects vor dem Rendern
// ausführen könnte (kein Middleware-Äquivalent) - der Schutz greift daher
// clientseitig nach dem ersten Render, sobald der Auth-Zustand geladen ist.
export function RequireAuth({ children }: { children: ReactNode }) {
  const { user, isLoading } = useAuth();
  const router = useRouter();

  useEffect(() => {
    if (!isLoading && !user) {
      router.replace("/login/");
    }
  }, [isLoading, user, router]);

  if (isLoading) {
    return <p className="page">Lade...</p>;
  }
  if (!user) {
    return null;
  }
  return <>{children}</>;
}
