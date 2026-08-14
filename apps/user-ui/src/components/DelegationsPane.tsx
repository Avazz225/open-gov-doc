"use client";

import { useCallback, useEffect, useState, type FormEvent } from "react";
import { useI18n } from "@/i18n";
import {
  ApiError,
  createDelegation,
  listActiveDelegationsForDeputy,
  listMyDelegations,
  lookupUserByUsername,
  revokeDelegation,
  type Delegation,
} from "@/lib/api";
import { usePrincipalNames } from "@/lib/usePrincipalNames";

function toDateTimeInputValue(daysFromNow: number): string {
  const date = new Date();
  date.setDate(date.getDate() + daysFromNow);
  date.setSeconds(0, 0);
  // <input type="datetime-local"> expects local time without a timezone suffix.
  return date.toISOString().slice(0, 16);
}

function isActive(delegation: Delegation): boolean {
  if (delegation.revoked_at !== null) return false;
  const now = Date.now();
  return new Date(delegation.starts_at).getTime() <= now && now <= new Date(delegation.ends_at).getTime();
}

// Delegation while absent (4.4a, P14-S11) - two sections: on the left
// the delegations I've set up myself (I am the represented person,
// "delegator") including create/revoke; on the right a read-only list of
// whom I am currently registered as a deputy for ("deputy") - the
// actual acting "on behalf of" doesn't happen here, but during task
// completion in the reviewer UI (see the "on behalf of" field there).
export function DelegationsPane({
  token,
  currentPrincipalId,
}: {
  token: string;
  currentPrincipalId: string;
}) {
  const { t } = useI18n();
  const [myDelegations, setMyDelegations] = useState<Delegation[]>([]);
  const [deputyFor, setDeputyFor] = useState<Delegation[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [deputyUsername, setDeputyUsername] = useState("");
  const [startsAt, setStartsAt] = useState(toDateTimeInputValue(0));
  const [endsAt, setEndsAt] = useState(toDateTimeInputValue(14));
  const [isCreating, setIsCreating] = useState(false);

  const reload = useCallback(async () => {
    if (!token || !currentPrincipalId) return;
    setIsLoading(true);
    setError(null);
    try {
      const [mine, deputy] = await Promise.all([
        listMyDelegations(token, currentPrincipalId),
        listActiveDelegationsForDeputy(token, currentPrincipalId),
      ]);
      setMyDelegations(mine);
      setDeputyFor(deputy);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("delegations.loadError"));
    } finally {
      setIsLoading(false);
    }
  }, [token, currentPrincipalId, t]);

  useEffect(() => {
    reload();
  }, [reload]);

  // Reverse identity resolution (P19-S4, ADR 0069) - shows names instead
  // of raw principal_id UUIDs in both lists below.
  const principalNames = usePrincipalNames(token, [
    ...myDelegations.map((d) => d.deputy_principal_id),
    ...deputyFor.map((d) => d.delegator_principal_id),
  ]);

  async function handleCreate(event: FormEvent) {
    event.preventDefault();
    if (!deputyUsername.trim() || !startsAt || !endsAt) return;
    setIsCreating(true);
    setError(null);
    try {
      const deputy = await lookupUserByUsername(token, deputyUsername.trim());
      await createDelegation(token, {
        deputyPrincipalId: deputy.id,
        startsAt: new Date(startsAt).toISOString(),
        endsAt: new Date(endsAt).toISOString(),
      });
      setDeputyUsername("");
      await reload();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("delegations.createError"));
    } finally {
      setIsCreating(false);
    }
  }

  async function handleRevoke(delegation: Delegation) {
    setError(null);
    try {
      await revokeDelegation(token, delegation.id);
      await reload();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("delegations.revokeError"));
    }
  }

  return (
    <section className="delegations-pane" aria-label={t("delegations.paneLabel")}>
      <h2 className="pane-heading">{t("delegations.heading")}</h2>
      <p className="hint">{t("delegations.hint")}</p>

      {error && (
        <p className="error-text" role="alert">
          {error}
        </p>
      )}

      <h3>{t("delegations.myDelegationsHeading")}</h3>
      <form className="inline-form" onSubmit={handleCreate}>
        <input
          type="text"
          placeholder={t("delegations.deputyUsernamePlaceholder")}
          value={deputyUsername}
          onChange={(e) => setDeputyUsername(e.target.value)}
        />
        <label>
          {t("delegations.startsAtLabel")}
          <input
            type="datetime-local"
            value={startsAt}
            onChange={(e) => setStartsAt(e.target.value)}
          />
        </label>
        <label>
          {t("delegations.endsAtLabel")}
          <input type="datetime-local" value={endsAt} onChange={(e) => setEndsAt(e.target.value)} />
        </label>
        <button type="submit" disabled={isCreating}>
          {t("delegations.createButton")}
        </button>
      </form>

      {isLoading ? (
        <p>{t("common.loading")}</p>
      ) : myDelegations.length === 0 ? (
        <p className="empty-state">{t("delegations.myDelegationsEmpty")}</p>
      ) : (
        <ul className="entry-list">
          {myDelegations.map((delegation) => (
            <li className="entry-row" key={delegation.id}>
              <span className="entry-name">
                {principalNames[delegation.deputy_principal_id] ?? delegation.deputy_principal_id}{" "}
                —{" "}
                {new Date(delegation.starts_at).toLocaleString()} –{" "}
                {new Date(delegation.ends_at).toLocaleString()}
                {delegation.revoked_at
                  ? ` (${t("delegations.revokedState")})`
                  : !isActive(delegation)
                    ? ` (${t("delegations.expiredState")})`
                    : ""}
              </span>
              {isActive(delegation) && (
                <span className="actions">
                  <button type="button" onClick={() => handleRevoke(delegation)}>
                    {t("delegations.revokeButton")}
                  </button>
                </span>
              )}
            </li>
          ))}
        </ul>
      )}

      <h3>{t("delegations.deputyForHeading")}</h3>
      <p className="hint">{t("delegations.deputyForHint")}</p>
      {deputyFor.length === 0 ? (
        <p className="empty-state">{t("delegations.deputyForEmpty")}</p>
      ) : (
        <ul className="entry-list">
          {deputyFor.map((delegation) => (
            <li className="entry-row" key={delegation.id}>
              <span className="entry-name">
                {principalNames[delegation.delegator_principal_id] ??
                  delegation.delegator_principal_id}{" "}
                —{" "}
                {t("delegations.deputyForUntil", {
                  date: new Date(delegation.ends_at).toLocaleString(),
                })}
              </span>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
