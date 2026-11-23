"use client";

import { useEffect, useState } from "react";
import { useI18n } from "@/i18n";
import {
  ApiError,
  applyFolderTemplate,
  getFolderTemplate,
  listFolderTemplates,
  listObjectTypes,
  type FolderTemplateDetail,
} from "@/lib/api";
import { useAuth } from "@/lib/auth-context";
import { useAllowedChildren } from "@/lib/structure";

// "Struktur aus Vorlage anlegen" (P91-S3, ADR 0255 Decision 5): the
// structure templates whose top folder may go below `target` - the same
// rules the server applies. Under a class with "nur aus Vorlagen" this is
// the way to create subfolders at all. Same `.modal-*` pattern as
// ReminderModal.tsx.
export function TemplateApplyModal({
  target,
  onApplied,
  onClose,
}: {
  target: { id: string; name: string };
  onApplied: (createdCount: number) => void;
  onClose: () => void;
}) {
  const { accessToken } = useAuth();
  const { t } = useI18n();
  const allowed = useAllowedChildren(accessToken, target.id);
  const [templates, setTemplates] = useState<FolderTemplateDetail[] | null>(null);
  const [typeNames, setTypeNames] = useState<Record<number, string>>({});
  const [error, setError] = useState<string | null>(null);
  const [applying, setApplying] = useState<string | null>(null);

  useEffect(() => {
    if (!accessToken) return;
    (async () => {
      try {
        const [summaries, types] = await Promise.all([
          listFolderTemplates(accessToken),
          listObjectTypes(accessToken, "folder"),
        ]);
        setTypeNames(Object.fromEntries(types.map((ot) => [ot.id, ot.name])));
        setTemplates(await Promise.all(summaries.map((s) => getFolderTemplate(accessToken, s.id))));
      } catch (err) {
        setError(err instanceof ApiError ? err.message : t("templates.loadError"));
        setTemplates([]);
      }
    })();
  }, [accessToken, t]);

  const fits = (template: FolderTemplateDetail) => {
    if (!allowed) return true;
    const typeId = template.structure.object_type_id;
    if (typeId === null) return allowed.untyped_folders;
    return allowed.folder_types.some((ft) => ft.id === typeId);
  };
  const offered = (templates ?? []).filter(fits);

  async function apply(template: FolderTemplateDetail) {
    if (!accessToken) return;
    setApplying(template.id);
    setError(null);
    try {
      const result = await applyFolderTemplate(accessToken, template.id, { targetParentId: target.id });
      onApplied(result.created_count);
      onClose();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("templates.applyError"));
    } finally {
      setApplying(null);
    }
  }

  const heading = t("templates.applyHeading", { name: target.name });
  const primaryBtn =
    "rounded-md border-0 bg-accent px-3 py-1.5 text-sm text-accent-fg transition-opacity hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-50 whitespace-nowrap";
  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div
        className="modal-content"
        role="dialog"
        aria-modal="true"
        aria-label={heading}
        onClick={(e) => e.stopPropagation()}
      >
        <div className="modal-header">
          <h2 className="m-0 text-base">{heading}</h2>
          <button type="button" className="modal-close" aria-label={t("common.close")} onClick={onClose}>
            ×
          </button>
        </div>
        {error && (
          <p className="text-danger" role="alert">
            {error}
          </p>
        )}
        {templates === null || allowed === undefined ? (
          <p className="text-sm opacity-70">{t("common.loading")}</p>
        ) : offered.length === 0 ? (
          <p className="text-sm">{t("templates.noneFit")}</p>
        ) : (
          <ul className="m-0 flex list-none flex-col gap-2 p-0">
            {offered.map((template) => (
              <li key={template.id} className="flex flex-wrap items-center gap-2">
                <span className="min-w-0 flex-1 break-words">
                  <strong>{template.name}</strong>
                  {template.structure.object_type_id !== null && (
                    <span className="opacity-80">
                      {" "}
                      ({typeNames[template.structure.object_type_id] ?? template.structure.object_type_id})
                    </span>
                  )}
                  {template.description && <span className="block text-sm opacity-80">{template.description}</span>}
                </span>
                <button
                  type="button"
                  className={primaryBtn}
                  disabled={applying !== null}
                  aria-label={t("templates.applyNamed", { name: template.name })}
                  onClick={() => apply(template)}
                >
                  {t("templates.applyShort")}
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}
