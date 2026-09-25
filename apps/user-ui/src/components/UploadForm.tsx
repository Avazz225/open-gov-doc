"use client";

import { useEffect, useState, type DragEvent, type FormEvent } from "react";
import { useI18n } from "@/i18n";
import {
  type LayoutData,
  type ObjectType,
  getObjectTypeLayout,
  listObjectTypes,
  uploadDocument,
} from "@/lib/api";
import { ApiError } from "@/lib/auth-context";
import { LayoutFormFields } from "./LayoutFormFields";

function attributeInputType(attrType: string | undefined): string {
  if (attrType === "integer" || attrType === "decimal") return "number";
  if (attrType === "date") return "date";
  return "text";
}

// Since P5b-S4, a document class can be selected for the first time during
// upload - previously the upload dialog didn't know about object types at
// all, only file/title. The attribute fields of the selected class are
// arranged via that class's "upload" form layout (2.2b), analogous to
// MetadataPanel (display) and SearchPane (search) - the same object-type
// hierarchy check (2.2a) as before is still handled server-side by the
// Document Service; this UI does not duplicate it.
//
// Since P5d-S2, a real modal dialog (user feedback) instead of a form shown
// inline in `ExplorerPane` - it additionally accepts drag-and-drop of files
// onto the entire dialog area, not just onto the `<input type="file">`.
// Reuses the already-existing but previously unused `.modal-backdrop`/
// `.modal-content` classes.
export function UploadForm({
  token,
  folderId,
  createdBy,
  onUploaded,
  onClose,
}: {
  token: string;
  folderId: string;
  createdBy: string;
  onUploaded: () => void;
  onClose: () => void;
}) {
  const { t } = useI18n();
  const [file, setFile] = useState<File | null>(null);
  const [title, setTitle] = useState("");
  const [objectTypes, setObjectTypes] = useState<ObjectType[]>([]);
  const [objectTypeId, setObjectTypeId] = useState("");
  const [layout, setLayout] = useState<LayoutData | null>(null);
  const [values, setValues] = useState<Record<string, string>>({});
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [isDragOver, setIsDragOver] = useState(false);
  // Draft / pre-registration lifecycle (post-roadmap phase 31 session 2,
  // ADR 0113) - opt-in, defaults to off so the reference number is still
  // assigned immediately unless a user deliberately chooses otherwise.
  const [draft, setDraft] = useState(false);

  useEffect(() => {
    if (!token) return;
    listObjectTypes(token, "document")
      .then(setObjectTypes)
      .catch(() => setObjectTypes([]));
  }, [token]);

  const selectedObjectType = objectTypes.find((ot) => String(ot.id) === objectTypeId);

  useEffect(() => {
    if (!token || !selectedObjectType) {
      setLayout(null);
      return;
    }
    getObjectTypeLayout(token, selectedObjectType.id, "upload")
      .then(setLayout)
      .catch(() => setLayout(null));
  }, [token, selectedObjectType]);

  function handleObjectTypeChange(value: string) {
    setObjectTypeId(value);
    setValues({});
  }

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    if (!file) return;
    setError(null);
    setSubmitting(true);
    try {
      await uploadDocument(token, {
        file,
        title: title || file.name,
        createdBy,
        folderId,
        objectTypeId: selectedObjectType?.id,
        attributes: selectedObjectType ? values : undefined,
        draft,
      });
      setFile(null);
      setTitle("");
      setObjectTypeId("");
      setValues({});
      setDraft(false);
      onUploaded();
      onClose();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("upload.error"));
    } finally {
      setSubmitting(false);
    }
  }

  function handleDragOver(event: DragEvent<HTMLDivElement>) {
    event.preventDefault();
    setIsDragOver(true);
  }

  function handleDragLeave(event: DragEvent<HTMLDivElement>) {
    event.preventDefault();
    setIsDragOver(false);
  }

  function handleDrop(event: DragEvent<HTMLDivElement>) {
    event.preventDefault();
    setIsDragOver(false);
    const dropped = event.dataTransfer.files?.[0];
    if (dropped) setFile(dropped);
  }

  const primaryBtn =
    "rounded-md border-0 bg-accent px-3 py-1.5 text-sm text-accent-fg transition-opacity hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-50";
  const secondaryBtn =
    "rounded-md border border-border bg-hover-bg px-3 py-1.5 text-sm text-fg transition-colors hover:bg-accent-bg disabled:cursor-not-allowed disabled:opacity-50";
  const fieldInput =
    "box-border rounded-md border border-border bg-bg px-2 py-1.5 text-sm text-fg outline-none transition-colors focus:border-accent focus:ring-2 focus:ring-accent-bg";
  const checkbox = "h-4 w-4 rounded border-border accent-accent";
  const fileInput =
    "text-sm file:mr-2 file:rounded-md file:border file:border-border file:bg-hover-bg file:px-3 file:py-1.5 file:text-sm file:text-fg file:transition-colors hover:file:bg-accent-bg";

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div
        className={`modal-content modal-content-wide upload-dropzone ${
          isDragOver ? "upload-dropzone-active" : ""
        }`}
        role="dialog"
        aria-modal="true"
        aria-label={t("upload.formLabel")}
        onClick={(e) => e.stopPropagation()}
        onDragOver={handleDragOver}
        onDragLeave={handleDragLeave}
        onDrop={handleDrop}
      >
        <div className="modal-header">
          <h2 className="m-0 text-base">{t("upload.formLabel")}</h2>
          <button type="button" className="modal-close" aria-label={t("upload.close")} onClick={onClose}>
            ×
          </button>
        </div>
        <p className="italic opacity-70">{t("upload.dropHint")}</p>

        <form onSubmit={handleSubmit} aria-label={t("upload.formLabel")}>
          <input
            type="file"
            className={fileInput}
            aria-label={t("upload.fileLabel")}
            onChange={(e) => setFile(e.target.files?.[0] ?? null)}
            required
          />
          {file && <p className="upload-selected-file">{file.name}</p>}
          <input
            type="text"
            className={fieldInput}
            placeholder={t("upload.titlePlaceholder")}
            value={title}
            onChange={(e) => setTitle(e.target.value)}
          />
          <label>
            {t("upload.objectTypeLabel")}
            <select
              className={fieldInput}
              value={objectTypeId}
              onChange={(e) => handleObjectTypeChange(e.target.value)}
            >
              <option value="">{t("upload.noObjectType")}</option>
              {objectTypes.map((ot) => (
                <option key={ot.id} value={ot.id}>
                  {ot.name}
                </option>
              ))}
            </select>
          </label>

          <label className="upload-draft-toggle">
            <input
              type="checkbox"
              className={checkbox}
              checked={draft}
              onChange={(e) => setDraft(e.target.checked)}
            />
            {t("upload.draftLabel")}
          </label>
          {draft && <p className="italic opacity-70">{t("upload.draftHint")}</p>}

          {selectedObjectType && layout && (
            <LayoutFormFields
              layout={layout}
              renderField={(field) => {
                const attribute = selectedObjectType.attributes.find(
                  (a) => a.name === field.attribute
                );
                return (
                  <label>
                    {field.label}
                    {field.required ? " *" : ""}
                    <input
                      type={attributeInputType(attribute?.type)}
                      className={fieldInput}
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
          )}

          <div className="flex gap-2">
            <button type="submit" className={primaryBtn} disabled={!file || submitting}>
              {submitting ? t("upload.submitting") : t("upload.submit")}
            </button>
            <button type="button" className={secondaryBtn} onClick={onClose}>
              {t("common.cancel")}
            </button>
          </div>
          {error && (
            <p className="text-danger" role="alert">
              {error}
            </p>
          )}
        </form>
      </div>
    </div>
  );
}
