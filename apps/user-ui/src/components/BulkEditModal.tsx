"use client";

import { useEffect, useState } from "react";
import { useI18n } from "@/i18n";
import {
  ApiError,
  getObjectType,
  getObjectTypeLayout,
  updateDocumentMetadata,
  updateFolderAttributes,
  type LayoutData,
  type ObjectType,
} from "@/lib/api";
import { LayoutFormFields } from "./LayoutFormFields";

export interface BulkEditItem {
  kind: "document" | "folder";
  id: string;
  name: string;
  object_type_id: number | null;
  attributes: Record<string, unknown>;
}

interface BulkEditResult {
  id: string;
  name: string;
  status: "success" | "error";
  message?: string;
}

function attributeInputType(attrType: string | undefined): string {
  if (attrType === "integer" || attrType === "decimal") return "number";
  if (attrType === "date") return "date";
  return "text";
}

// A 400 with constraint errors (4.5) returns `detail: {"errors": [...]}` -
// `ApiError.message` therefore contains the JSON-stringified body (see
// lib/api.ts's `extractErrorMessage`, which only special-cases a plain
// string `detail`). For a readable result line, this attempts to
// extract the individual error messages again instead of showing the
// raw JSON string - if that fails (not JSON, e.g. 404), the original
// message is left unchanged.
function readableErrorMessage(message: string): string {
  try {
    const parsed = JSON.parse(message);
    if (parsed && Array.isArray(parsed.errors)) {
      return parsed.errors.join("; ");
    }
  } catch {
    // not JSON - message remains unchanged.
  }
  return message;
}

// Bulk metadata editing (8, P14-S12) - deliberately WITHOUT its own
// backend endpoint: runs for each selected object individually via the
// already-existing single-object PATCH endpoints (`updateDocumentMetadata`/
// `updateFolderAttributes`), so exactly the same constraint checking applies
// as for a single edit (4.5, no second validation implementation).
// The result is deliberately NOT all-or-nothing - each object is attempted
// individually, the result (success/failure per object) is then displayed
// clearly summarized, exactly as required by the concept.
//
// Deliberate restriction: only possible if the selection is homogeneous (all
// documents OR all folders, all with the same, set object type) - only
// then is there a single attribute schema that can be meaningfully filled
// in together. A field left blank means "leave unchanged for this object",
// not "clear" - only fields that were actually filled in are merged per
// object into its already-existing attributes (the server replaces
// `attributes` entirely, no server-side merge).
export function BulkEditModal({
  token,
  items,
  onClose,
  onDone,
}: {
  token: string;
  items: BulkEditItem[];
  onClose: () => void;
  onDone: () => void;
}) {
  const { t } = useI18n();
  const [objectType, setObjectType] = useState<ObjectType | null>(null);
  const [layout, setLayout] = useState<LayoutData | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [values, setValues] = useState<Record<string, string>>({});
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [results, setResults] = useState<BulkEditResult[] | null>(null);

  const firstObjectTypeId = items[0]?.object_type_id ?? null;
  const isHomogeneous =
    items.length > 0 &&
    items.every((item) => item.kind === items[0].kind && item.object_type_id === firstObjectTypeId);

  useEffect(() => {
    if (!isHomogeneous || firstObjectTypeId === null) return;
    setLoadError(null);
    Promise.all([
      getObjectType(token, firstObjectTypeId),
      getObjectTypeLayout(token, firstObjectTypeId, "display"),
    ])
      .then(([fetchedObjectType, fetchedLayout]) => {
        setObjectType(fetchedObjectType);
        setLayout(fetchedLayout);
      })
      .catch(() => setLoadError(t("bulkEdit.loadObjectTypeError")));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isHomogeneous, firstObjectTypeId, token]);

  async function handleSubmit() {
    setIsSubmitting(true);
    const collected: BulkEditResult[] = [];
    const touched = Object.entries(values).filter(([, v]) => v.trim() !== "");
    for (const item of items) {
      const mergedAttributes = { ...item.attributes };
      for (const [key, value] of touched) {
        mergedAttributes[key] = value;
      }
      try {
        if (item.kind === "document") {
          await updateDocumentMetadata(token, item.id, { attributes: mergedAttributes });
        } else {
          await updateFolderAttributes(token, item.id, mergedAttributes);
        }
        collected.push({ id: item.id, name: item.name, status: "success" });
      } catch (err) {
        collected.push({
          id: item.id,
          name: item.name,
          status: "error",
          message: err instanceof ApiError ? readableErrorMessage(err.message) : t("common.actionError"),
        });
      }
    }
    setResults(collected);
    setIsSubmitting(false);
    onDone();
  }

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div
        className="modal-content"
        role="dialog"
        aria-modal="true"
        aria-label={t("bulkEdit.heading", { count: items.length })}
        onClick={(e) => e.stopPropagation()}
      >
        <div className="modal-header">
          <h2 className="pane-heading">{t("bulkEdit.heading", { count: items.length })}</h2>
          <button type="button" className="modal-close" aria-label={t("common.close")} onClick={onClose}>
            ×
          </button>
        </div>

        {results ? (
          <>
            <ul className="entry-list">
              {results.map((result) => (
                <li className="entry-row" key={result.id}>
                  <span className="entry-name">
                    <span className={`badge ${result.status === "success" ? "ok" : "down"}`}>
                      {result.status === "success" ? t("bulkEdit.success") : t("bulkEdit.failure")}
                    </span>{" "}
                    {result.name}
                  </span>
                  {result.message && <span className="hint">{result.message}</span>}
                </li>
              ))}
            </ul>
            <div className="actions">
              <button type="button" onClick={onClose}>
                {t("common.close")}
              </button>
            </div>
          </>
        ) : !isHomogeneous ? (
          <p className="error-text" role="alert">
            {t("bulkEdit.notHomogeneous")}
          </p>
        ) : firstObjectTypeId === null ? (
          <p className="empty-state">{t("bulkEdit.noObjectType")}</p>
        ) : (
          <>
            <p className="hint">{t("bulkEdit.hint")}</p>
            {loadError && (
              <p className="error-text" role="alert">
                {loadError}
              </p>
            )}
            {objectType && layout && (
              <LayoutFormFields
                layout={layout}
                renderField={(field) => {
                  const attribute = objectType.attributes.find((a) => a.name === field.attribute);
                  return (
                    <label>
                      {field.label}
                      <input
                        type={attributeInputType(attribute?.type)}
                        value={values[field.attribute] ?? ""}
                        placeholder={t("bulkEdit.leaveBlankPlaceholder")}
                        onChange={(e) =>
                          setValues((prev) => ({ ...prev, [field.attribute]: e.target.value }))
                        }
                      />
                    </label>
                  );
                }}
              />
            )}
            <div className="actions">
              <button type="button" onClick={handleSubmit} disabled={isSubmitting || !layout}>
                {isSubmitting ? t("bulkEdit.submitting") : t("bulkEdit.submit")}
              </button>
              <button type="button" onClick={onClose}>
                {t("common.cancel")}
              </button>
            </div>
          </>
        )}
      </div>
    </div>
  );
}
