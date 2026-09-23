"use client";

import { useEffect, useState, type FormEvent } from "react";
import { useI18n } from "@/i18n";
import { ApiError, getObjectType, type ObjectTypeAttribute } from "@/lib/api";

// Inline metadata editing (3.3a: "instead of switching applications for
// that") - deliberately simplified compared to user-ui's `LayoutFormFields`
// (2.2b): a simple text field per attribute instead of type-specific
// widgets/layout arrangement, appropriate for the narrow task pane width.
// See docs/services/office-addin.md "Open Points".
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
    <form onSubmit={handleSubmit} className="flex flex-col gap-2">
      <div className="flex flex-col gap-1">
        <label htmlFor="ogdoc-title" className="text-sm font-medium text-fg">
          {t("metadataForm.titleLabel")}
        </label>
        <input
          id="ogdoc-title"
          type="text"
          value={titleValue}
          onChange={(e) => setTitleValue(e.target.value)}
          required
          className="box-border w-full rounded-md border border-border bg-bg px-2 py-1.5 text-fg outline-none transition-colors focus:border-accent focus:ring-2 focus:ring-accent-bg"
        />
      </div>
      {attributeSchema.map((attribute) => (
        <div key={attribute.name} className="flex flex-col gap-1">
          <label htmlFor={`ogdoc-attr-${attribute.name}`} className="text-sm font-medium text-fg">
            {attribute.name}
          </label>
          <input
            id={`ogdoc-attr-${attribute.name}`}
            type="text"
            value={attrValues[attribute.name] ?? ""}
            onChange={(e) =>
              setAttrValues((prev) => ({ ...prev, [attribute.name]: e.target.value }))
            }
            className="box-border w-full rounded-md border border-border bg-bg px-2 py-1.5 text-fg outline-none transition-colors focus:border-accent focus:ring-2 focus:ring-accent-bg"
          />
        </div>
      ))}
      {error && (
        <p className="text-sm text-danger" role="alert">
          {error}
        </p>
      )}
      <button
        type="submit"
        disabled={isSaving}
        className="rounded-md border-0 bg-accent px-3 py-1 text-accent-fg transition-opacity hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-50"
      >
        {isSaving ? t("metadataForm.saving") : submitLabel}
      </button>
    </form>
  );
}
