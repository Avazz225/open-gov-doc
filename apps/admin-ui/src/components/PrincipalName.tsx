"use client";

import { lookupUserByUsername } from "@/lib/api";
import { useAuth } from "@/lib/auth-context";
import {
  isPrincipalId,
  principalName,
  principalTitle,
  usePrincipalNames,
} from "@/lib/usePrincipalNames";

// A recorded actor shown as a name (P86-S2). Since P85 (ADR 0240) the
// `actor`/`*_by` fields admin-ui displays hold principals - a user's
// Keycloak `sub`. Each use resolves through `usePrincipalNames`' shared
// cache, so a table with many rows by the same person costs one request.
// The raw id stays reachable on hover.
export function usePrincipalName(value: string | null | undefined): {
  name: string;
  title: string | undefined;
} {
  const { accessToken } = useAuth();
  const names = usePrincipalNames(accessToken ?? "", [value]);
  return { name: principalName(names, value), title: principalTitle(names, value) };
}

export function PrincipalName({
  value,
  fallback = "—",
}: {
  value: string | null | undefined;
  fallback?: string;
}) {
  const { name, title } = usePrincipalName(value);
  if (!value) return <>{fallback}</>;
  return <span title={title}>{name}</span>;
}

// An actor filter typed as a username matches the principal it names
// (events record principals since P85). A UUID, an unknown name or a
// service/federated actor is used as typed - older events still carry
// usernames, so an unknown name may still match those.
export async function resolveActorFilter(token: string, input: string): Promise<string> {
  if (!input || isPrincipalId(input)) return input;
  try {
    return (await lookupUserByUsername(token, input)).id;
  } catch {
    return input;
  }
}
