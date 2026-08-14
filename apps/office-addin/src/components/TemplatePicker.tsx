"use client";

import { useEffect, useState } from "react";
import { useI18n } from "@/i18n";
import {
  ApiError,
  listDocumentsInFolder,
  listRootFolders,
  type DocumentSummary,
} from "@/lib/api";
import { TEMPLATE_LIBRARY_FOLDER_NAME } from "@/lib/config";

// Central, role-based template library (3.3a) - deliberately NO new
// storage/permission mechanism: a template is an ordinary
// document in the conventionally named root folder "Vorlagen"
// (TEMPLATE_LIBRARY_FOLDER_NAME); role-basedness follows automatically
// from the already-existing folder read-permission check (permission-service) -
// whoever isn't allowed to read the folder won't see it (or its search results from
// search-service) anyway.
export function TemplatePicker({
  token,
  onUseTemplate,
  disabled,
}: {
  token: string;
  onUseTemplate: (template: DocumentSummary) => void;
  disabled: boolean;
}) {
  const { t } = useI18n();
  const [templates, setTemplates] = useState<DocumentSummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!token) return;
    (async () => {
      try {
        const rootFolders = await listRootFolders(token);
        const folder = rootFolders.find((f) => f.name === TEMPLATE_LIBRARY_FOLDER_NAME);
        if (!folder) {
          setTemplates([]);
          return;
        }
        setTemplates(await listDocumentsInFolder(token, folder.id));
      } catch (err) {
        setError(err instanceof ApiError ? err.message : t("templatePicker.loadError"));
        setTemplates([]);
      }
    })();
  }, [token, t]);

  return (
    <section className="section" aria-label={t("templatePicker.heading")}>
      <h2>{t("templatePicker.heading")}</h2>
      <p className="hint">{t("templatePicker.hint", { folder: TEMPLATE_LIBRARY_FOLDER_NAME })}</p>
      {error && (
        <p className="error-text" role="alert">
          {error}
        </p>
      )}
      {templates === null ? (
        <p className="empty-state">{t("common.loading")}</p>
      ) : templates.length === 0 ? (
        <p className="empty-state">{t("templatePicker.empty")}</p>
      ) : (
        <ul className="entry-list">
          {templates.map((template) => (
            <li className="entry-row" key={template.id}>
              <span>{template.title}</span>
              <button type="button" disabled={disabled} onClick={() => onUseTemplate(template)}>
                {t("templatePicker.useButton")}
              </button>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
