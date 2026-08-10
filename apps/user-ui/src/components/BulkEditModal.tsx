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

// Ein 400 mit Constraint-Fehlern (4.5) liefert `detail: {"errors": [...]}` -
// `ApiError.message` enthält dafür den JSON-stringifizierten Body (siehe
// lib/api.ts's `extractErrorMessage`, das nur einen reinen String-`detail`
// speziell behandelt). Für eine lesbare Ergebniszeile wird hier versucht,
// die einzelnen Fehlermeldungen wieder herauszulösen, statt den rohen
// JSON-String anzuzeigen - schlägt das fehl (kein JSON, z. B. 404), bleibt
// die ursprüngliche Nachricht unverändert stehen.
function readableErrorMessage(message: string): string {
  try {
    const parsed = JSON.parse(message);
    if (parsed && Array.isArray(parsed.errors)) {
      return parsed.errors.join("; ");
    }
  } catch {
    // kein JSON - message bleibt unverändert.
  }
  return message;
}

// Sammelbearbeitung von Metadaten (8, P14-S12) - bewusst OHNE eigenen
// Backend-Endpunkt: läuft für jedes ausgewählte Objekt einzeln über die
// bereits bestehenden Einzel-PATCH-Endpunkte (`updateDocumentMetadata`/
// `updateFolderAttributes`), damit exakt dieselbe Constraint-Prüfung wie bei
// einer Einzeländerung greift (4.5, keine zweite Validierungsimplementierung).
// Ergebnis ist bewusst NICHT alles-oder-nichts - jedes Objekt wird einzeln
// versucht, das Ergebnis (erfolgreich/fehlgeschlagen je Objekt) danach klar
// zusammengefasst angezeigt, exakt wie im Konzept gefordert.
//
// Bewusste Einschränkung: nur möglich, wenn die Auswahl homogen ist (alle
// Dokumente ODER alle Ordner, alle mit demselben, gesetzten Objekttyp) - nur
// dann gibt es ein einziges Attribut-Schema, das sich sinnvoll gemeinsam
// befüllen lässt. Ein leer gelassenes Feld bedeutet "für dieses Objekt
// unverändert lassen", nicht "leeren" - nur tatsächlich befüllte Felder
// werden pro Objekt in dessen bereits bestehende Attribute gemergt (der
// Server ersetzt `attributes` vollständig, kein serverseitiges Merge).
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
