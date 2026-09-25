"use client";

import { useState, type FormEvent } from "react";
import { useI18n } from "@/i18n";
import {
  ApiError,
  compareConfig,
  exportConfigAt,
  loginAt,
  type CategoryDelta,
  type CompareResult,
} from "@/lib/api";
import { useAuth } from "@/lib/auth-context";
import { useInstallation } from "@/lib/installation-context";

// Cross-installation config compare (7.5, Phase 52 Session 2, ADR 0040's own
// deferred "later UI session"). Reuses `POST /config/compare` exactly as
// `ConfigPackages.tsx` does - that endpoint has no notion of "installation A
// vs B" at all, it only ever diffs two `ConfigDocument` payloads already in
// hand (ADR 0040's own deliberate "no automated cross-installation fetch"
// decision). "Base" is always the currently active installation's own live
// export (config-service defaults to that when `base` is omitted from the
// request, same as `ConfigPackages.tsx`'s preview). "Compare" is fetched via
// a ONE-OFF login scoped to just this screen (`loginAt`/`exportConfigAt`,
// never touching the active installation's own session/`gatewayBaseUrl`) -
// the admin picks a known installation (`InstallationManager.tsx`'s own
// list) and authenticates against it directly, mirroring the CLI flow ADR
// 0040 describes ("two dms config export calls, each with its own login").
function formatDeltaValue(value: unknown): string {
  if (value === null || value === undefined) return "–";
  if (typeof value === "string") return value;
  return JSON.stringify(value);
}

// Per-field diff view (Phase 58 Session 2, ADR 0040's own self-documented
// "smaller, still-open follow-up") - `delta.differing` already carried
// {base, compare} per field since P14-S1 (see `CategoryDelta`'s own type),
// only the frontend never rendered it, showing item NAMES only. A native
// <details>/<summary> per item needs no extra expand-state, the standard
// "click to reveal a table" idiom.
function DifferingItem({ name, fields }: { name: string; fields: Record<string, { base: unknown; compare: unknown }> }) {
  const { t } = useI18n();
  const fieldNames = Object.keys(fields);
  return (
    <details>
      <summary>{name}</summary>
      <table className="data-table">
        <thead>
          <tr>
            <th>{t("configCompare.diffField")}</th>
            <th>{t("configCompare.diffBase")}</th>
            <th>{t("configCompare.diffCompare")}</th>
          </tr>
        </thead>
        <tbody>
          {fieldNames.map((field) => (
            <tr key={field}>
              <td>{field}</td>
              <td>{formatDeltaValue(fields[field].base)}</td>
              <td>{formatDeltaValue(fields[field].compare)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </details>
  );
}

function DeltaTable({ category, delta }: { category: string; delta: CategoryDelta }) {
  const { t } = useI18n();
  const differingNames = Object.keys(delta.differing);
  const hasChanges = delta.only_in_compare.length > 0 || differingNames.length > 0;
  return (
    <div className="rounded-lg border border-border p-4 mb-6" key={category}>
      <h3>{category}</h3>
      {!hasChanges && delta.only_in_base.length === 0 && (
        <p className="text-sm opacity-80">{t("configCompare.deltaNoChanges")}</p>
      )}
      {delta.only_in_compare.length > 0 && (
        <p>
          <strong>{t("configCompare.deltaOnlyInCompare")}:</strong>{" "}
          {delta.only_in_compare.join(", ")}
        </p>
      )}
      {differingNames.length > 0 && (
        <div>
          <strong>{t("configCompare.deltaDiffering")}:</strong>
          {differingNames.map((name) => (
            <DifferingItem key={name} name={name} fields={delta.differing[name]} />
          ))}
        </div>
      )}
      {delta.only_in_base.length > 0 && (
        <p className="text-sm opacity-80">
          <strong>{t("configCompare.deltaOnlyInBase")}:</strong> {delta.only_in_base.join(", ")}
        </p>
      )}
    </div>
  );
}

export function ConfigCompare() {
  const { accessToken } = useAuth();
  const { installations, activeInstallation } = useInstallation();
  const { t } = useI18n();

  const otherInstallations = installations.filter((i) => i.id !== activeInstallation.id);
  const [compareInstallationId, setCompareInstallationId] = useState(
    otherInstallations[0]?.id ?? ""
  );
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [ignoreRegex, setIgnoreRegex] = useState("");

  const [isComparing, setIsComparing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<CompareResult | null>(null);

  const primaryBtn =
    "rounded-md border-0 bg-accent px-3 py-1.5 text-sm text-accent-fg transition-opacity hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-50";
  const fieldInput =
    "box-border rounded-md border border-border bg-bg px-2 py-1.5 text-sm text-fg outline-none transition-colors focus:border-accent focus:ring-2 focus:ring-accent-bg";

  async function handleCompare(event: FormEvent) {
    event.preventDefault();
    if (!accessToken) return;
    const target = installations.find((i) => i.id === compareInstallationId);
    if (!target) return;
    setIsComparing(true);
    setError(null);
    setResult(null);
    try {
      const tokens = await loginAt(target.gatewayBaseUrl, username, password);
      const compareDoc = await exportConfigAt(target.gatewayBaseUrl, tokens.access_token);
      setResult(await compareConfig(accessToken, compareDoc, undefined, ignoreRegex || undefined));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("configCompare.compareError"));
    } finally {
      setIsComparing(false);
    }
  }

  return (
    <div>
      <div className="rounded-lg border border-border p-4 mb-6">
        <p className="text-sm opacity-80">{t("configCompare.hint")}</p>
        <p className="text-sm opacity-80">
          {t("configCompare.baseIsActive", { name: activeInstallation.name })}
        </p>

        {otherInstallations.length === 0 ? (
          <p className="italic opacity-70">{t("configCompare.noOtherInstallations")}</p>
        ) : (
          <form
            aria-label={t("configCompare.formLabel")}
            className="form-grid"
            onSubmit={handleCompare}
          >
            <label>
              {t("configCompare.compareInstallation")}
              <select
                value={compareInstallationId}
                onChange={(e) => setCompareInstallationId(e.target.value)}
                required
                className={fieldInput}
              >
                {otherInstallations.map((i) => (
                  <option key={i.id} value={i.id}>
                    {i.name}
                  </option>
                ))}
              </select>
            </label>
            <label>
              {t("configCompare.username")}
              <input
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                required
                className={fieldInput}
              />
            </label>
            <label>
              {t("configCompare.password")}
              <input
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                required
                className={fieldInput}
              />
            </label>
            <label>
              {t("configCompare.ignoreRegex")}
              <input
                value={ignoreRegex}
                onChange={(e) => setIgnoreRegex(e.target.value)}
                placeholder={t("configCompare.ignoreRegexPlaceholder")}
                className={fieldInput}
              />
            </label>
            <p className="text-sm opacity-80">{t("configCompare.ignoreRegexHint")}</p>
            <button type="submit" disabled={isComparing} className={primaryBtn}>
              {t("configCompare.compare")}
            </button>
          </form>
        )}
      </div>

      {error && (
        <p className="text-danger" role="alert">
          {error}
        </p>
      )}

      {result && (
        <div>
          <h2>{t("configCompare.resultTitle")}</h2>
          {Object.entries(result.categories).map(([category, delta]) => (
            <DeltaTable key={category} category={category} delta={delta} />
          ))}
        </div>
      )}
    </div>
  );
}
