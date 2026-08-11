"use client";

import { useCallback, useState } from "react";
import { useI18n } from "@/i18n";
import {
  ApiError,
  CONFIG_CATEGORIES,
  compareConfig,
  exportConfig,
  importConfig,
  type CategoryDelta,
  type CompareResult,
  type ConfigCategory,
  type ConfigDocument,
  type ConfigImportActionResult,
} from "@/lib/api";
import { useAuth } from "@/lib/auth-context";

// Konfigurationspakete (14.1, P17-S1): ein Paket ist ein `ConfigDocument`
// (7.3-Format, seit P12-S3 bestehend) mit optionalem, rein beschreibendem
// `manifest`. Diese Seite ist die erste Admin-UI-Anbindung von config-service
// überhaupt - vorher gab es hierfür KEINE Oberfläche, nur der (seither in
// `POST /config/fleet-import` umbenannte) Fleet-Agent-Zugriffsweg konnte
// importieren, siehe ADR zu P17-S1. Die Anwendung selbst nutzt ausschließlich
// den bereits bestehenden, abgesicherten Konfigurationsimport - kein neuer
// Anwendungsmechanismus, nur eine Bedienoberfläche dafür.
function presentCategories(doc: ConfigDocument): ConfigCategory[] {
  return CONFIG_CATEGORIES.filter((category) => {
    const value = doc[category];
    return value !== null && value !== undefined;
  });
}

function categoryCount(doc: ConfigDocument, category: ConfigCategory): number | null {
  const value = doc[category];
  if (Array.isArray(value)) return value.length;
  if (value !== null && value !== undefined) return 1;
  return null;
}

interface DeltaTableProps {
  category: string;
  delta: CategoryDelta;
}

function DeltaTable({ category, delta }: DeltaTableProps) {
  const { t } = useI18n();
  const differingNames = Object.keys(delta.differing);
  const hasChanges = delta.only_in_compare.length > 0 || differingNames.length > 0;
  return (
    <div className="card" key={category}>
      <h3>{category}</h3>
      {!hasChanges && delta.only_in_base.length === 0 && (
        <p className="hint">{t("configPackages.deltaNoChanges")}</p>
      )}
      {delta.only_in_compare.length > 0 && (
        <p>
          <strong>{t("configPackages.deltaOnlyInPackage")}:</strong>{" "}
          {delta.only_in_compare.join(", ")}
        </p>
      )}
      {differingNames.length > 0 && (
        <p>
          <strong>{t("configPackages.deltaDiffering")}:</strong> {differingNames.join(", ")}
        </p>
      )}
      {delta.only_in_base.length > 0 && (
        <p className="hint">
          <strong>{t("configPackages.deltaOnlyInSystem")}:</strong> {delta.only_in_base.join(", ")}
        </p>
      )}
    </div>
  );
}

export function ConfigPackages() {
  const { accessToken } = useAuth();
  const { t } = useI18n();

  const [fileName, setFileName] = useState<string | null>(null);
  const [configDoc, setConfigDoc] = useState<ConfigDocument | null>(null);
  const [parseError, setParseError] = useState<string | null>(null);

  const [compareResult, setCompareResult] = useState<CompareResult | null>(null);
  const [isComparing, setIsComparing] = useState(false);
  const [compareError, setCompareError] = useState<string | null>(null);

  const [importResult, setImportResult] = useState<ConfigImportActionResult | null>(null);
  const [isImporting, setIsImporting] = useState(false);
  const [importError, setImportError] = useState<string | null>(null);

  const [isExporting, setIsExporting] = useState(false);
  const [exportError, setExportError] = useState<string | null>(null);

  async function handleFileChange(fileList: FileList | null) {
    const file = fileList?.[0] ?? null;
    setCompareResult(null);
    setImportResult(null);
    setCompareError(null);
    setImportError(null);
    if (!file) {
      setFileName(null);
      setConfigDoc(null);
      setParseError(null);
      return;
    }
    setFileName(file.name);
    try {
      // FileReader statt `file.text()` - breiter unterstützt, u. a. in
      // jsdom (Testumgebung) tatsächlich implementiert.
      const text = await new Promise<string>((resolve, reject) => {
        const reader = new FileReader();
        reader.onload = () => resolve(String(reader.result ?? ""));
        reader.onerror = () => reject(reader.error);
        reader.readAsText(file);
      });
      const parsed = JSON.parse(text) as ConfigDocument;
      if (typeof parsed !== "object" || parsed === null || !("schema_version" in parsed)) {
        throw new Error(t("configPackages.invalidDocument"));
      }
      setConfigDoc(parsed);
      setParseError(null);
    } catch {
      setConfigDoc(null);
      setParseError(t("configPackages.invalidDocument"));
    }
  }

  const handlePreview = useCallback(async () => {
    if (!accessToken || !configDoc) return;
    setIsComparing(true);
    setCompareError(null);
    setCompareResult(null);
    try {
      // Nur die im Paket tatsächlich vorhandenen Kategorien vergleichen -
      // ohne Einschränkung würde jede vom Paket nicht berührte Kategorie
      // (leer im Dokument) gegen den vollständigen Live-Export verglichen
      // und als "nur im aktuellen System" gelistet - bei vielen bereits
      // angesammelten Objekten (Rollen, Workflows, ...) unbrauchbar viel
      // Rauschen für ein Paket, das nur eine einzelne Kategorie mitbringt.
      setCompareResult(await compareConfig(accessToken, configDoc, presentCategories(configDoc)));
    } catch (err) {
      setCompareError(err instanceof ApiError ? err.message : t("common.loadError"));
    } finally {
      setIsComparing(false);
    }
  }, [accessToken, configDoc, t]);

  const handleApply = useCallback(async () => {
    if (!accessToken || !configDoc) return;
    setIsImporting(true);
    setImportError(null);
    setImportResult(null);
    try {
      setImportResult(await importConfig(accessToken, configDoc));
    } catch (err) {
      setImportError(err instanceof ApiError ? err.message : t("common.loadError"));
    } finally {
      setIsImporting(false);
    }
  }, [accessToken, configDoc, t]);

  const handleExportCurrent = useCallback(async () => {
    if (!accessToken) return;
    setIsExporting(true);
    setExportError(null);
    try {
      const current = await exportConfig(accessToken);
      const blob = new Blob([JSON.stringify(current, null, 2)], { type: "application/json" });
      const url = URL.createObjectURL(blob);
      const link = window.document.createElement("a");
      link.href = url;
      link.download = `dms-config-export-${new Date().toISOString().slice(0, 10)}.json`;
      link.click();
      URL.revokeObjectURL(url);
    } catch (err) {
      setExportError(err instanceof ApiError ? err.message : t("common.loadError"));
    } finally {
      setIsExporting(false);
    }
  }, [accessToken, t]);

  return (
    <div>
      <div className="card">
        <p className="hint">{t("configPackages.hint")}</p>
        <div className="actions">
          <button type="button" onClick={handleExportCurrent} disabled={isExporting}>
            {t("configPackages.exportCurrent")}
          </button>
        </div>
        {exportError && (
          <p className="error-text" role="alert">
            {exportError}
          </p>
        )}
      </div>

      <div className="card">
        <label>
          {t("configPackages.selectFile")}
          <input
            type="file"
            accept="application/json"
            onChange={(event) => handleFileChange(event.target.files)}
          />
        </label>
        {parseError && (
          <p className="error-text" role="alert">
            {parseError}
          </p>
        )}
        {configDoc && (
          <div>
            <p className="hint">
              {t("configPackages.loadedFile")}: {fileName}
            </p>
            {configDoc.manifest ? (
              <div className="card">
                <h3>{configDoc.manifest.name}</h3>
                <p className="hint">
                  {t("configPackages.manifestVersion")}: {configDoc.manifest.version} ·{" "}
                  {t("configPackages.manifestCompatibility")}: {configDoc.manifest.compatibility_range}
                </p>
                {configDoc.manifest.description && <p>{configDoc.manifest.description}</p>}
                {(configDoc.manifest.origin || configDoc.manifest.license) && (
                  <p className="hint">
                    {configDoc.manifest.origin}
                    {configDoc.manifest.origin && configDoc.manifest.license ? " · " : ""}
                    {configDoc.manifest.license}
                  </p>
                )}
              </div>
            ) : (
              <p className="hint">{t("configPackages.noManifest")}</p>
            )}
            <ul>
              {presentCategories(configDoc).map((category) => (
                <li key={category}>
                  {category}
                  {(() => {
                    const count = categoryCount(configDoc, category);
                    return count !== null ? ` (${count})` : "";
                  })()}
                </li>
              ))}
            </ul>
            <div className="actions">
              <button type="button" onClick={handlePreview} disabled={isComparing}>
                {t("configPackages.preview")}
              </button>
              <button type="button" onClick={handleApply} disabled={isImporting}>
                {t("configPackages.apply")}
              </button>
            </div>
          </div>
        )}
      </div>

      {compareError && (
        <p className="error-text" role="alert">
          {compareError}
        </p>
      )}
      {compareResult && (
        <div>
          <h2>{t("configPackages.previewResultTitle")}</h2>
          {Object.entries(compareResult.categories).map(([category, delta]) => (
            <DeltaTable key={category} category={category} delta={delta} />
          ))}
        </div>
      )}

      {importError && (
        <p className="error-text" role="alert">
          {importError}
        </p>
      )}
      {importResult && importResult.status === "pending_approval" && (
        <p className="hint">{t("configPackages.pendingApproval")}</p>
      )}
      {importResult && importResult.result && (
        <div className="card">
          <h2>{t("configPackages.applyResultTitle")}</h2>
          <table className="data-table">
            <thead>
              <tr>
                <th>{t("configPackages.resultCategory")}</th>
                <th>{t("configPackages.resultCreated")}</th>
                <th>{t("configPackages.resultUpdated")}</th>
                <th>{t("configPackages.resultSkipped")}</th>
                <th>{t("configPackages.resultErrors")}</th>
              </tr>
            </thead>
            <tbody>
              {Object.entries(importResult.result.results).map(([category, result]) => (
                <tr key={category}>
                  <td>{category}</td>
                  <td>{result.created}</td>
                  <td>{result.updated}</td>
                  <td>{result.skipped}</td>
                  <td>{result.errors.length > 0 ? result.errors.join("; ") : "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
