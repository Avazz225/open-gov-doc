"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { useI18n } from "@/i18n";
import {
  ApiError,
  getObjectTypeLayout,
  listObjectTypes,
  putObjectTypeLayout,
  resetObjectTypeLayout,
  type LayoutData,
  type LayoutPurpose,
  type LayoutRow,
  type ObjectType,
} from "@/lib/api";
import { useAuth } from "@/lib/auth-context";

const PURPOSES: LayoutPurpose[] = ["display", "search", "upload"];

// Pure row operations (move/delete/add row) instead of freely dragging
// individual fields between rows: a field is removed from its row (thereby
// becoming "available" again) and placed into another row via that row's
// own add-selector - two unambiguous steps instead of one ambiguous
// move gesture, without a drag&drop library (no visual browser verification
// possible in this environment, see PROGRESS.md).
function cloneRows(rows: LayoutRow[]): LayoutRow[] {
  return rows.map((row) => ({ columns: row.columns.map((field) => ({ ...field })) }));
}

function purposeLabelKey(purpose: LayoutPurpose): string {
  return `layoutDesigner.purpose${purpose.charAt(0).toUpperCase()}${purpose.slice(1)}`;
}

export function LayoutDesigner() {
  const { accessToken } = useAuth();
  const { t } = useI18n();
  const [objectTypes, setObjectTypes] = useState<ObjectType[]>([]);
  const [objectTypeId, setObjectTypeId] = useState<number | null>(null);
  const [purpose, setPurpose] = useState<LayoutPurpose>("display");
  const [layout, setLayout] = useState<LayoutData | null>(null);
  const [addAttributeByRow, setAddAttributeByRow] = useState<Record<number, string>>({});
  const [error, setError] = useState<string | null>(null);
  const [status, setStatus] = useState<string | null>(null);

  const loadObjectTypes = useCallback(async () => {
    if (!accessToken) return;
    try {
      const types = await listObjectTypes(accessToken);
      setObjectTypes(types);
      setObjectTypeId((prev) => prev ?? types[0]?.id ?? null);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("common.loadError"));
    }
  }, [accessToken, t]);

  useEffect(() => {
    loadObjectTypes();
  }, [loadObjectTypes]);

  const loadLayout = useCallback(async () => {
    if (!accessToken || objectTypeId === null) return;
    setError(null);
    setStatus(null);
    try {
      const data = await getObjectTypeLayout(accessToken, objectTypeId, purpose);
      setLayout(data);
      setAddAttributeByRow({});
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("layoutDesigner.loadError"));
      setLayout(null);
    }
  }, [accessToken, objectTypeId, purpose, t]);

  useEffect(() => {
    loadLayout();
  }, [loadLayout]);

  const selectedObjectType = useMemo(
    () => objectTypes.find((ot) => ot.id === objectTypeId) ?? null,
    [objectTypes, objectTypeId]
  );

  const usedAttributes = useMemo(
    () => new Set(layout?.rows.flatMap((row) => row.columns.map((c) => c.attribute)) ?? []),
    [layout]
  );

  const availableAttributes = useMemo(
    () => (selectedObjectType?.attributes ?? []).filter((a) => !usedAttributes.has(a.name)),
    [selectedObjectType, usedAttributes]
  );

  function updateRows(updater: (rows: LayoutRow[]) => LayoutRow[]) {
    setLayout((prev) => (prev ? { ...prev, rows: updater(cloneRows(prev.rows)) } : prev));
  }

  function moveRow(index: number, direction: -1 | 1) {
    updateRows((rows) => {
      const target = index + direction;
      if (target < 0 || target >= rows.length) return rows;
      [rows[index], rows[target]] = [rows[target], rows[index]];
      return rows;
    });
  }

  function removeRow(index: number) {
    updateRows((rows) => rows.filter((_, i) => i !== index));
  }

  function addRow() {
    updateRows((rows) => [...rows, { columns: [] }]);
  }

  function removeField(rowIndex: number, colIndex: number) {
    updateRows((rows) => {
      rows[rowIndex].columns.splice(colIndex, 1);
      return rows[rowIndex].columns.length === 0 ? rows.filter((_, i) => i !== rowIndex) : rows;
    });
  }

  function moveFieldWithinRow(rowIndex: number, colIndex: number, direction: -1 | 1) {
    updateRows((rows) => {
      const columns = rows[rowIndex].columns;
      const target = colIndex + direction;
      if (target < 0 || target >= columns.length) return rows;
      [columns[colIndex], columns[target]] = [columns[target], columns[colIndex]];
      return rows;
    });
  }

  function updateFieldLabel(rowIndex: number, colIndex: number, label: string) {
    updateRows((rows) => {
      rows[rowIndex].columns[colIndex].label = label;
      return rows;
    });
  }

  function updateFieldRequired(rowIndex: number, colIndex: number, required: boolean) {
    updateRows((rows) => {
      rows[rowIndex].columns[colIndex].required = required;
      return rows;
    });
  }

  function addFieldToRow(rowIndex: number) {
    const attributeName = addAttributeByRow[rowIndex];
    if (!attributeName) return;
    updateRows((rows) => {
      rows[rowIndex].columns.push({ attribute: attributeName, label: attributeName, required: false });
      return rows;
    });
    setAddAttributeByRow((prev) => ({ ...prev, [rowIndex]: "" }));
  }

  function updateBreakpoint(value: number) {
    setLayout((prev) => (prev ? { ...prev, responsive_breakpoint_px: value } : prev));
  }

  async function handleSave() {
    if (!accessToken || objectTypeId === null || !layout) return;
    setError(null);
    setStatus(null);
    try {
      const rows = layout.rows.filter((row) => row.columns.length > 0);
      const saved = await putObjectTypeLayout(accessToken, objectTypeId, purpose, {
        rows,
        responsiveBreakpointPx: layout.responsive_breakpoint_px,
      });
      setLayout(saved);
      setStatus(t("layoutDesigner.save"));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("layoutDesigner.saveError"));
    }
  }

  async function handleReset() {
    if (!accessToken || objectTypeId === null) return;
    setError(null);
    setStatus(null);
    try {
      await resetObjectTypeLayout(accessToken, objectTypeId, purpose);
      await loadLayout();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("layoutDesigner.resetError"));
    }
  }

  return (
    <section className="card">
      <h2>{t("layoutDesigner.heading")}</h2>
      <p className="hint">{t("layoutDesigner.hint")}</p>
      {error && (
        <p className="error-text" role="alert">
          {error}
        </p>
      )}
      {status && <p role="status">{status}</p>}

      {objectTypes.length === 0 ? (
        <p className="empty-state">{t("layoutDesigner.noObjectTypes")}</p>
      ) : (
        <>
          <div className="form-grid">
            <label>
              {t("layoutDesigner.objectTypeLabel")}
              <select
                value={objectTypeId ?? ""}
                onChange={(e) => setObjectTypeId(Number(e.target.value))}
              >
                {objectTypes.map((ot) => (
                  <option key={ot.id} value={ot.id}>
                    {ot.name}
                  </option>
                ))}
              </select>
            </label>
            <label>
              {t("layoutDesigner.purposeLabel")}
              <select value={purpose} onChange={(e) => setPurpose(e.target.value as LayoutPurpose)}>
                {PURPOSES.map((p) => (
                  <option key={p} value={p}>
                    {t(purposeLabelKey(p))}
                  </option>
                ))}
              </select>
            </label>
            <label>
              {t("layoutDesigner.breakpointLabel")}
              <input
                type="number"
                value={layout?.responsive_breakpoint_px ?? 600}
                onChange={(e) => updateBreakpoint(Number(e.target.value))}
              />
            </label>
          </div>

          {layout && (
            <>
              <p>
                <span className={`badge ${layout.is_custom ? "ok" : ""}`}>
                  {layout.is_custom
                    ? t("layoutDesigner.customBadge")
                    : t("layoutDesigner.generatedBadge")}
                </span>
              </p>

              {layout.rows.length === 0 && (
                <p className="empty-state">{t("layoutDesigner.emptyLayout")}</p>
              )}

              {layout.rows.map((row, rowIndex) => (
                <div className="layout-row" key={rowIndex}>
                  <div className="actions">
                    <strong>{t("layoutDesigner.rowHeading", { n: rowIndex + 1 })}</strong>
                    <button type="button" onClick={() => moveRow(rowIndex, -1)} disabled={rowIndex === 0}>
                      {t("layoutDesigner.moveUp")}
                    </button>
                    <button
                      type="button"
                      onClick={() => moveRow(rowIndex, 1)}
                      disabled={rowIndex === layout.rows.length - 1}
                    >
                      {t("layoutDesigner.moveDown")}
                    </button>
                    <button type="button" onClick={() => removeRow(rowIndex)}>
                      {t("layoutDesigner.removeRow")}
                    </button>
                  </div>
                  {row.columns.map((field, colIndex) => (
                    <div className="layout-field" key={field.attribute}>
                      <code>{field.attribute}</code>
                      <label>
                        {t("layoutDesigner.fieldLabel")}
                        <input
                          value={field.label}
                          onChange={(e) => updateFieldLabel(rowIndex, colIndex, e.target.value)}
                        />
                      </label>
                      <label className="checkbox-label">
                        <input
                          type="checkbox"
                          checked={field.required}
                          onChange={(e) => updateFieldRequired(rowIndex, colIndex, e.target.checked)}
                        />
                        {t("layoutDesigner.fieldRequired")}
                      </label>
                      <button
                        type="button"
                        onClick={() => moveFieldWithinRow(rowIndex, colIndex, -1)}
                        disabled={colIndex === 0}
                      >
                        ◀
                      </button>
                      <button
                        type="button"
                        onClick={() => moveFieldWithinRow(rowIndex, colIndex, 1)}
                        disabled={colIndex === row.columns.length - 1}
                      >
                        ▶
                      </button>
                      <button type="button" onClick={() => removeField(rowIndex, colIndex)}>
                        {t("layoutDesigner.removeField")}
                      </button>
                    </div>
                  ))}
                  {availableAttributes.length > 0 && (
                    <div className="layout-field">
                      <select
                        value={addAttributeByRow[rowIndex] ?? ""}
                        onChange={(e) =>
                          setAddAttributeByRow((prev) => ({ ...prev, [rowIndex]: e.target.value }))
                        }
                      >
                        <option value="">{t("layoutDesigner.addFieldPlaceholder")}</option>
                        {availableAttributes.map((a) => (
                          <option key={a.name} value={a.name}>
                            {a.name}
                          </option>
                        ))}
                      </select>
                      <button type="button" onClick={() => addFieldToRow(rowIndex)}>
                        {t("layoutDesigner.addFieldButton")}
                      </button>
                    </div>
                  )}
                </div>
              ))}

              <button type="button" onClick={addRow}>
                {t("layoutDesigner.addRow")}
              </button>

              <div className="actions">
                <button type="button" onClick={handleSave}>
                  {t("layoutDesigner.save")}
                </button>
                <button type="button" onClick={handleReset}>
                  {t("layoutDesigner.reset")}
                </button>
              </div>
            </>
          )}
        </>
      )}
    </section>
  );
}
