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
      });
      setFile(null);
      setTitle("");
      setObjectTypeId("");
      setValues({});
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
          <h2 className="pane-heading">{t("upload.formLabel")}</h2>
          <button type="button" className="modal-close" aria-label={t("upload.close")} onClick={onClose}>
            ×
          </button>
        </div>
        <p className="empty-state">{t("upload.dropHint")}</p>

        <form onSubmit={handleSubmit} aria-label={t("upload.formLabel")}>
          <input
            type="file"
            aria-label={t("upload.fileLabel")}
            onChange={(e) => setFile(e.target.files?.[0] ?? null)}
            required
          />
          {file && <p className="upload-selected-file">{file.name}</p>}
          <input
            type="text"
            placeholder={t("upload.titlePlaceholder")}
            value={title}
            onChange={(e) => setTitle(e.target.value)}
          />
          <label>
            {t("upload.objectTypeLabel")}
            <select value={objectTypeId} onChange={(e) => handleObjectTypeChange(e.target.value)}>
              <option value="">{t("upload.noObjectType")}</option>
              {objectTypes.map((ot) => (
                <option key={ot.id} value={ot.id}>
                  {ot.name}
                </option>
              ))}
            </select>
          </label>

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

          <div className="actions">
            <button type="submit" disabled={!file || submitting}>
              {submitting ? t("upload.submitting") : t("upload.submit")}
            </button>
            <button type="button" onClick={onClose}>
              {t("common.cancel")}
            </button>
          </div>
          {error && (
            <p className="error-text" role="alert">
              {error}
            </p>
          )}
        </form>
      </div>
    </div>
  );
}
