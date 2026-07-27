"use client";

import { useEffect, useState, type FormEvent } from "react";
import { useI18n } from "@/i18n";
import {
  ApiError,
  type DocumentSummary,
  type ObjectType,
  getObjectType,
  updateDocumentMetadata,
} from "@/lib/api";
import { useAuth } from "@/lib/auth-context";

function attributeInputType(attrType: string | undefined): string {
  if (attrType === "integer" || attrType === "decimal") return "number";
  if (attrType === "date") return "date";
  return "text";
}

// Untere linke Spalte des 3-Spalten-Layouts (Nutzer-Feedback nach P4-S3, 8):
// Metadaten des über die Tabs ausgewählten Dokuments. Attribut-Formfelder
// werden aus dem Objekttyp-Schema (2.2, Object-Type Service) generiert -
// ohne Objekttyp gibt es nur den Titel zu bearbeiten. Speichert über den
// seit P4-S4 neuen `PATCH /documents/{id}` (Document Service).
export function MetadataPanel({
  document: activeDocument,
  onSaved,
}: {
  document: DocumentSummary | null;
  onSaved: (updated: DocumentSummary) => void;
}) {
  const { accessToken } = useAuth();
  const { t } = useI18n();
  const [objectType, setObjectType] = useState<ObjectType | null>(null);
  const [title, setTitle] = useState("");
  const [values, setValues] = useState<Record<string, string>>({});
  const [error, setError] = useState<string | null>(null);
  const [isSaving, setIsSaving] = useState(false);

  useEffect(() => {
    setError(null);
    setObjectType(null);
    if (!activeDocument) {
      setTitle("");
      setValues({});
      return;
    }
    setTitle(activeDocument.title);
    setValues(
      Object.fromEntries(
        Object.entries(activeDocument.attributes).map(([key, value]) => [key, String(value)])
      )
    );
    if (activeDocument.object_type_id !== null && accessToken) {
      getObjectType(accessToken, activeDocument.object_type_id)
        .then(setObjectType)
        .catch(() => setError(t("metadata.loadObjectTypeError")));
    }
  }, [activeDocument, accessToken, t]);

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    if (!accessToken || !activeDocument) return;
    setError(null);
    setIsSaving(true);
    try {
      const updated = await updateDocumentMetadata(accessToken, activeDocument.id, {
        title,
        attributes: values,
      });
      onSaved(updated);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("metadata.saveError"));
    } finally {
      setIsSaving(false);
    }
  }

  if (!activeDocument) {
    return (
      <section className="metadata-panel" aria-label={t("metadata.paneLabel")}>
        <p className="empty-state">{t("metadata.noSelection")}</p>
      </section>
    );
  }

  return (
    <section className="metadata-panel" aria-label={t("metadata.paneLabel")}>
      <h2 className="pane-heading">{t("metadata.heading")}</h2>
      <form aria-label={t("metadata.formLabel")} onSubmit={handleSubmit}>
        <label>
          {t("metadata.titleLabel")}
          <input value={title} onChange={(e) => setTitle(e.target.value)} required />
        </label>

        {objectType ? (
          objectType.attributes.map((attr) => (
            <label key={attr.name}>
              {attr.name}
              {attr.required ? " *" : ""}
              <input
                type={attributeInputType(attr.type)}
                value={values[attr.name] ?? ""}
                required={attr.required}
                onChange={(e) => setValues((prev) => ({ ...prev, [attr.name]: e.target.value }))}
              />
            </label>
          ))
        ) : activeDocument.object_type_id === null ? (
          <p className="empty-state">{t("metadata.noObjectType")}</p>
        ) : null}

        {error && (
          <p className="error-text" role="alert">
            {error}
          </p>
        )}

        <button type="submit" disabled={isSaving}>
          {isSaving ? t("metadata.saving") : t("common.save")}
        </button>
      </form>
    </section>
  );
}
