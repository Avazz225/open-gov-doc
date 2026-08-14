"use client";

import { useEffect, useState, type FormEvent } from "react";
import { useI18n } from "@/i18n";
import {
  ApiError,
  type DocumentSummary,
  type LayoutData,
  type ObjectType,
  getObjectType,
  getObjectTypeLayout,
  updateDocumentMetadata,
} from "@/lib/api";
import { useAuth } from "@/lib/auth-context";
import { KENNZEICHEN_ATTRIBUTE } from "@/lib/kennzeichen";
import { LayoutFormFields } from "./LayoutFormFields";
import { RetentionPanel } from "./RetentionPanel";
import { SignaturesPanel } from "./SignaturesPanel";

// Must match the document-service setting `kennzeichen_admin_role` (default,
// P5e-S2) - only principals with this role may change an already-assigned
// reference number, everyone else sees it read-only.
const KENNZEICHEN_ADMIN_ROLE = "dms-admin";

function attributeInputType(attrType: string | undefined): string {
  if (attrType === "integer" || attrType === "decimal") return "number";
  if (attrType === "date") return "date";
  return "text";
}

// Lower-left column of the 3-column layout (user feedback after P4-S3, 8):
// metadata of the document selected via the tabs. Since P5b-S4 the
// attribute form fields are no longer hardwired (one field per row in
// attribute order), but arranged via the object type's "display" form
// layout (2.2b) - `LayoutFormFields` renders the row/column grid,
// `objectType.attributes` still supplies the type (for the input field
// type) and value range. Without an object type, only the title remains
// editable. Saves via the `PATCH /documents/{id}` endpoint (document
// service) available since P4-S4.
export function MetadataPanel({
  document: activeDocument,
  onSaved,
  onSigned,
}: {
  document: DocumentSummary | null;
  onSaved: (updated: DocumentSummary) => void;
  onSigned?: (documentId: string) => void;
}) {
  const { accessToken, user } = useAuth();
  const { t } = useI18n();
  const isKennzeichenAdmin = Boolean(user?.realm_roles.includes(KENNZEICHEN_ADMIN_ROLE));
  const [objectType, setObjectType] = useState<ObjectType | null>(null);
  const [layout, setLayout] = useState<LayoutData | null>(null);
  const [title, setTitle] = useState("");
  const [values, setValues] = useState<Record<string, string>>({});
  const [error, setError] = useState<string | null>(null);
  const [isSaving, setIsSaving] = useState(false);

  useEffect(() => {
    setError(null);
    setObjectType(null);
    setLayout(null);
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
      const objectTypeId = activeDocument.object_type_id;
      Promise.all([
        getObjectType(accessToken, objectTypeId),
        getObjectTypeLayout(accessToken, objectTypeId, "display"),
      ])
        .then(([fetchedObjectType, fetchedLayout]) => {
          setObjectType(fetchedObjectType);
          setLayout(fetchedLayout);
        })
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

        {KENNZEICHEN_ATTRIBUTE in activeDocument.attributes && (
          <label>
            {t("metadata.kennzeichenLabel")}
            <input
              value={values[KENNZEICHEN_ATTRIBUTE] ?? ""}
              readOnly={!isKennzeichenAdmin}
              disabled={!isKennzeichenAdmin}
              onChange={(e) =>
                setValues((prev) => ({ ...prev, [KENNZEICHEN_ATTRIBUTE]: e.target.value }))
              }
            />
          </label>
        )}
        {KENNZEICHEN_ATTRIBUTE in activeDocument.attributes && !isKennzeichenAdmin && (
          <p className="hint">{t("metadata.kennzeichenReadOnlyHint")}</p>
        )}

        {objectType && layout ? (
          <LayoutFormFields
            layout={layout}
            renderField={(field) => {
              const attribute = objectType.attributes.find((a) => a.name === field.attribute);
              return (
                <label>
                  {field.label}
                  {field.required ? " *" : ""}
                  <input
                    type={attributeInputType(attribute?.type)}
                    value={values[field.attribute] ?? ""}
                    required={field.required}
                    onChange={(e) =>
                      setValues((prev) => ({ ...prev, [field.attribute]: e.target.value }))
                    }
                  />
                </label>
              );
            }}
          />
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

      <SignaturesPanel document={activeDocument} onSigned={onSigned} />
      <RetentionPanel document={activeDocument} />
    </section>
  );
}
