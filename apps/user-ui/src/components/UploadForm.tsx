"use client";

import { useState, type FormEvent } from "react";
import { useI18n } from "@/i18n";
import { uploadDocument } from "@/lib/api";
import { ApiError } from "@/lib/auth-context";

export function UploadForm({
  token,
  folderId,
  createdBy,
  onUploaded,
}: {
  token: string;
  folderId: string;
  createdBy: string;
  onUploaded: () => void;
}) {
  const { t } = useI18n();
  const [file, setFile] = useState<File | null>(null);
  const [title, setTitle] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

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
      });
      setFile(null);
      setTitle("");
      onUploaded();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("upload.error"));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <form onSubmit={handleSubmit} aria-label={t("upload.formLabel")}>
      <input
        type="file"
        aria-label={t("upload.fileLabel")}
        onChange={(e) => setFile(e.target.files?.[0] ?? null)}
        required
      />
      <input
        type="text"
        placeholder={t("upload.titlePlaceholder")}
        value={title}
        onChange={(e) => setTitle(e.target.value)}
      />
      <button type="submit" disabled={!file || submitting}>
        {submitting ? t("upload.submitting") : t("upload.submit")}
      </button>
      {error && (
        <p className="error-text" role="alert">
          {error}
        </p>
      )}
    </form>
  );
}
