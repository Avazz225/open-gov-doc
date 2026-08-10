"use client";

import { useEffect, useState, type FormEvent } from "react";
import { useI18n } from "@/i18n";
import { ApiError, getObjectType, type ObjectTypeAttribute } from "@/lib/api";

// Inline-Metadaten-Bearbeitung (3.3a: "statt dafür die Anwendung zu
// wechseln") - bewusst vereinfacht gegenüber user-ui's `LayoutFormFields`
// (2.2b): ein einfaches Textfeld je Attribut statt typspezifischer Widgets/
// Layout-Anordnung, angemessen für die schmale Taskpane-Breite. Siehe
// docs/services/office-addin.md "Offene Punkte".
export function MetadataForm({
  token,
  objectTypeId,
  title,
  attributes,
  submitLabel,
  onSubmit,
}: {
  token: string;
  objectTypeId: number | null;
  title: string;
  attributes: Record<string, unknown>;
  submitLabel: string;
  onSubmit: (values: { title: string; attributes: Record<string, string> }) => Promise<void>;
}) {
  const { t } = useI18n();
  const [attributeSchema, setAttributeSchema] = useState<ObjectTypeAttribute[]>([]);
  const [titleValue, setTitleValue] = useState(title);
  const [attrValues, setAttrValues] = useState<Record<string, string>>(
    Object.fromEntries(Object.entries(attributes).map(([k, v]) => [k, String(v ?? "")]))
  );
  const [isSaving, setIsSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setTitleValue(title);
  }, [title]);

  useEffect(() => {
    if (objectTypeId === null) {
      setAttributeSchema([]);
      return;
    }
    getObjectType(token, objectTypeId)
      .then((ot) => setAttributeSchema(ot.attributes))
      .catch(() => setAttributeSchema([]));
  }, [token, objectTypeId]);

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    setIsSaving(true);
    setError(null);
    try {
      await onSubmit({ title: titleValue, attributes: attrValues });
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("metadataForm.saveError"));
    } finally {
      setIsSaving(false);
    }
  }

  return (
    <form onSubmit={handleSubmit}>
      <label htmlFor="ogdoc-title">{t("metadataForm.titleLabel")}</label>
      <input
        id="ogdoc-title"
        type="text"
        value={titleValue}
        onChange={(e) => setTitleValue(e.target.value)}
        required
      />
      {attributeSchema.map((attribute) => (
        <div key={attribute.name}>
          <label htmlFor={`ogdoc-attr-${attribute.name}`}>{attribute.name}</label>
          <input
            id={`ogdoc-attr-${attribute.name}`}
            type="text"
            value={attrValues[attribute.name] ?? ""}
            onChange={(e) =>
              setAttrValues((prev) => ({ ...prev, [attribute.name]: e.target.value }))
            }
          />
        </div>
      ))}
      {error && (
        <p className="error-text" role="alert">
          {error}
        </p>
      )}
      <button type="submit" className="primary" disabled={isSaving}>
        {isSaving ? t("metadataForm.saving") : submitLabel}
      </button>
    </form>
  );
}
