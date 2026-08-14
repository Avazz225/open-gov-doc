"use client";

import { useCallback, useEffect, useState } from "react";
import { useI18n } from "@/i18n";
import {
  acquireLock,
  ApiError,
  checkinVersion,
  createDocument,
  downloadDocumentContent,
  getDocument,
  releaseLock,
  updateDocumentMetadata,
  type DocumentSummary,
  type SearchResult,
} from "@/lib/api";
import { useAuth } from "@/lib/auth-context";
import {
  base64ToBlob,
  blobToBase64,
  clearLinkedDocument,
  getCurrentDocumentAsBase64,
  getLinkedDocument,
  replaceDocumentContentFromBase64,
  setLinkedDocument,
  type LinkedDocument,
} from "@/lib/office";
import { DocumentPicker } from "./DocumentPicker";
import { MetadataForm } from "./MetadataForm";
import { TemplatePicker } from "./TemplatePicker";
import { WorkflowPanel } from "./WorkflowPanel";

interface PendingTemplate {
  templateId: string;
  templateVersionNumber: number;
  objectTypeId: number | null;
  attributes: Record<string, unknown>;
  suggestedTitle: string;
}

// Orchestrates the feature scope required by 3.3a - open/save, inline
// metadata, workflow start/continuation, template library. The actual
// "which DMS document is this" state does NOT live here, but in the Word
// file itself (`Office.context.document.settings`, see lib/office.ts) -
// after closing/reopening the file (with the add-in activated), the link
// is automatically restored.
export function TaskPane() {
  const { t } = useI18n();
  const { accessToken, user } = useAuth();
  const token = accessToken ?? "";

  const [linked, setLinked] = useState<LinkedDocument | null>(null);
  const [pendingTemplate, setPendingTemplate] = useState<PendingTemplate | null>(null);
  const [document, setDocumentState] = useState<DocumentSummary | null>(null);
  const [lockOwner, setLockOwner] = useState<string | null>(null);
  const [isBusy, setIsBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const sessionId =
    typeof window !== "undefined" && "crypto" in window
      ? window.crypto.randomUUID()
      : `${Date.now()}`;

  const loadLinkedDetail = useCallback(
    async (documentId: string) => {
      try {
        setDocumentState(await getDocument(token, documentId));
      } catch (err) {
        setError(err instanceof ApiError ? err.message : t("taskPane.loadError"));
      }
    },
    [token, t]
  );

  useEffect(() => {
    if (!token) return;
    const existing = getLinkedDocument();
    setLinked(existing);
    if (existing) loadLinkedDetail(existing.documentId);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token]);

  async function handleOpenExisting(result: SearchResult) {
    setIsBusy(true);
    setError(null);
    setNotice(null);
    try {
      const detail = await getDocument(token, result.id);
      const content = await downloadDocumentContent(token, result.id);
      await replaceDocumentContentFromBase64(await blobToBase64(content));

      try {
        await acquireLock(token, result.id, {
          lockedBy: user?.username ?? "",
          sessionId,
        });
        setLockOwner(user?.username ?? null);
      } catch (lockErr) {
        setLockOwner(null);
        setNotice(
          lockErr instanceof ApiError
            ? t("taskPane.lockConflict", { detail: lockErr.message })
            : t("taskPane.lockConflictGeneric")
        );
      }

      await setLinkedDocument(result.id, detail.current_version_number);
      setLinked({ documentId: result.id, versionNumber: detail.current_version_number });
      setDocumentState(detail);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("taskPane.openError"));
    } finally {
      setIsBusy(false);
    }
  }

  async function handleUseTemplate(template: DocumentSummary) {
    setIsBusy(true);
    setError(null);
    setNotice(null);
    try {
      const content = await downloadDocumentContent(token, template.id);
      await replaceDocumentContentFromBase64(await blobToBase64(content));
      setPendingTemplate({
        templateId: template.id,
        templateVersionNumber: template.current_version_number,
        objectTypeId: template.object_type_id,
        attributes: template.attributes,
        suggestedTitle: template.title,
      });
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("taskPane.templateError"));
    } finally {
      setIsBusy(false);
    }
  }

  async function handleSaveNewFromTemplate(values: {
    title: string;
    attributes: Record<string, string>;
  }) {
    if (!pendingTemplate) return;
    const base64 = await getCurrentDocumentAsBase64();
    const created = await createDocument(token, {
      file: base64ToBlob(
        base64,
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
      ),
      title: values.title,
      createdBy: user?.username ?? "",
      objectTypeId: pendingTemplate.objectTypeId ?? undefined,
      attributes: values.attributes,
      derivedFromDocumentId: pendingTemplate.templateId,
      derivedFromVersionNumber: pendingTemplate.templateVersionNumber,
    });
    await setLinkedDocument(created.id, created.current_version_number);
    setLinked({ documentId: created.id, versionNumber: created.current_version_number });
    setDocumentState(created);
    setPendingTemplate(null);

    try {
      await acquireLock(token, created.id, { lockedBy: user?.username ?? "", sessionId });
      setLockOwner(user?.username ?? null);
    } catch {
      setLockOwner(null);
    }
  }

  async function handleSaveMetadata(values: { title: string; attributes: Record<string, string> }) {
    if (!linked) return;
    const updated = await updateDocumentMetadata(token, linked.documentId, {
      title: values.title,
      attributes: values.attributes,
    });
    setDocumentState(updated);
    setNotice(t("taskPane.metadataSaved"));
  }

  async function handleSaveVersion() {
    if (!linked) return;
    setIsBusy(true);
    setError(null);
    setNotice(null);
    try {
      const base64 = await getCurrentDocumentAsBase64();
      const blob = base64ToBlob(
        base64,
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
      );
      const result = await checkinVersion(token, linked.documentId, {
        file: blob,
        expectedBaseVersionNumber: linked.versionNumber,
        createdBy: user?.username ?? "",
      });
      const newVersion = result.version.version_number;
      await setLinkedDocument(linked.documentId, newVersion);
      setLinked({ documentId: linked.documentId, versionNumber: newVersion });
      setNotice(result.is_conflict ? t("taskPane.savedAsConflictCopy") : t("taskPane.saved"));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("taskPane.saveError"));
    } finally {
      setIsBusy(false);
    }
  }

  async function handleUnlink() {
    if (linked && lockOwner) {
      try {
        await releaseLock(token, linked.documentId, user?.username ?? "");
      } catch {
        /* The lock may already have expired/been released otherwise -
           unlinking should not fail because of that. */
      }
    }
    await clearLinkedDocument();
    setLinked(null);
    setDocumentState(null);
    setLockOwner(null);
    setPendingTemplate(null);
    setError(null);
    setNotice(null);
  }

  if (pendingTemplate) {
    return (
      <div>
        <h2>{t("taskPane.newFromTemplateHeading")}</h2>
        {error && (
          <p className="error-text" role="alert">
            {error}
          </p>
        )}
        <MetadataForm
          token={token}
          objectTypeId={pendingTemplate.objectTypeId}
          title={pendingTemplate.suggestedTitle}
          attributes={pendingTemplate.attributes}
          submitLabel={t("taskPane.saveNewDocumentButton")}
          onSubmit={handleSaveNewFromTemplate}
        />
      </div>
    );
  }

  if (linked && document) {
    const canManage = lockOwner !== null;
    return (
      <div>
        <section className="section">
          <h2>{document.title}</h2>
          {!canManage && <p className="hint">{t("taskPane.readOnlyHint")}</p>}
          {notice && <p className="hint">{notice}</p>}
          {error && (
            <p className="error-text" role="alert">
              {error}
            </p>
          )}
          <MetadataForm
            token={token}
            objectTypeId={document.object_type_id}
            title={document.title}
            attributes={document.attributes}
            submitLabel={t("metadataForm.saveButton")}
            onSubmit={handleSaveMetadata}
          />
          <div className="actions">
            <button
              type="button"
              className="primary"
              disabled={isBusy || !canManage}
              onClick={handleSaveVersion}
            >
              {t("taskPane.saveVersionButton")}
            </button>
            <button type="button" disabled={isBusy} onClick={handleUnlink}>
              {t("taskPane.unlinkButton")}
            </button>
          </div>
        </section>
        <WorkflowPanel token={token} documentId={linked.documentId} currentUsername={user?.username ?? ""} />
      </div>
    );
  }

  return (
    <div>
      {error && (
        <p className="error-text" role="alert">
          {error}
        </p>
      )}
      <DocumentPicker token={token} onOpen={handleOpenExisting} disabled={isBusy} />
      <TemplatePicker token={token} onUseTemplate={handleUseTemplate} disabled={isBusy} />
    </div>
  );
}
