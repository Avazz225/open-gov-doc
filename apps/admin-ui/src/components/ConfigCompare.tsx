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
function DeltaTable({ category, delta }: { category: string; delta: CategoryDelta }) {
  const { t } = useI18n();
  const differingNames = Object.keys(delta.differing);
  const hasChanges = delta.only_in_compare.length > 0 || differingNames.length > 0;
  return (
    <div className="card" key={category}>
      <h3>{category}</h3>
      {!hasChanges && delta.only_in_base.length === 0 && (
        <p className="hint">{t("configCompare.deltaNoChanges")}</p>
      )}
      {delta.only_in_compare.length > 0 && (
        <p>
          <strong>{t("configCompare.deltaOnlyInCompare")}:</strong>{" "}
          {delta.only_in_compare.join(", ")}
        </p>
      )}
      {differingNames.length > 0 && (
        <p>
          <strong>{t("configCompare.deltaDiffering")}:</strong> {differingNames.join(", ")}
        </p>
      )}
      {delta.only_in_base.length > 0 && (
        <p className="hint">
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

  const [isComparing, setIsComparing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<CompareResult | null>(null);

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
      setResult(await compareConfig(accessToken, compareDoc));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("configCompare.compareError"));
    } finally {
      setIsComparing(false);
    }
  }

  return (
    <div>
      <div className="card">
        <p className="hint">{t("configCompare.hint")}</p>
        <p className="hint">
          {t("configCompare.baseIsActive", { name: activeInstallation.name })}
        </p>

        {otherInstallations.length === 0 ? (
          <p className="empty-state">{t("configCompare.noOtherInstallations")}</p>
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
              <input value={username} onChange={(e) => setUsername(e.target.value)} required />
            </label>
            <label>
              {t("configCompare.password")}
              <input
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                required
              />
            </label>
            <button type="submit" disabled={isComparing}>
              {t("configCompare.compare")}
            </button>
          </form>
        )}
      </div>

      {error && (
        <p className="error-text" role="alert">
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
