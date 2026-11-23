"use client";

import { useI18n } from "@/i18n";
import type { StructureFields } from "@/lib/api";
import { StructureListEditor } from "./StructureListEditor";

// Folder classes only (P91-S3, ADR 0255): what may be created below this
// class, the defaults for clients that create without a type (WebDAV, CMIS,
// mail, a plain "Neuer Ordner"), and "only from templates".
export function ObjectTypeStructureFields({
  fields,
  onChange,
  folderClassNames,
  documentTypeNames,
  fieldInput,
  checkbox,
}: {
  fields: StructureFields;
  onChange: (next: StructureFields) => void;
  folderClassNames: string[];
  documentTypeNames: string[];
  fieldInput: string;
  checkbox: string;
}) {
  const { t } = useI18n();
  const patch = (p: Partial<StructureFields>) => onChange({ ...fields, ...p });
  const folderDefaults = fields.allowedChildFolderTypes ?? folderClassNames;
  const documentDefaults = fields.allowedDocumentTypes ?? documentTypeNames;

  return (
    <section className="mt-4" aria-labelledby="object-type-structure-heading">
      <h3 id="object-type-structure-heading">{t("structure.classHeading")}</h3>
      <p className="text-sm opacity-80">{t("structure.classHint")}</p>
      <div className="grid gap-3 md:grid-cols-2">
        <StructureListEditor
          legend={t("structure.childFolders")}
          value={fields.allowedChildFolderTypes}
          options={folderClassNames}
          onChange={(next) =>
            patch({
              allowedChildFolderTypes: next,
              defaultChildFolderType:
                next !== null && fields.defaultChildFolderType && !next.includes(fields.defaultChildFolderType)
                  ? null
                  : fields.defaultChildFolderType,
            })
          }
          checkbox={checkbox}
        />
        <StructureListEditor
          legend={t("structure.childDocuments")}
          value={fields.allowedDocumentTypes}
          options={documentTypeNames}
          onChange={(next) =>
            patch({
              allowedDocumentTypes: next,
              defaultDocumentType:
                next !== null && fields.defaultDocumentType && !next.includes(fields.defaultDocumentType)
                  ? null
                  : fields.defaultDocumentType,
            })
          }
          checkbox={checkbox}
        />
      </div>
      <div className="form-grid mt-3">
        <label>
          {t("structure.defaultFolder")}
          <select
            value={fields.defaultChildFolderType ?? ""}
            onChange={(e) => patch({ defaultChildFolderType: e.target.value || null })}
            className={`${fieldInput} max-w-[16rem]`}
          >
            <option value="">{t("structure.noDefault")}</option>
            {folderDefaults.map((name) => (
              <option key={name} value={name}>
                {name}
              </option>
            ))}
          </select>
        </label>
        <label>
          {t("structure.defaultDocument")}
          <select
            value={fields.defaultDocumentType ?? ""}
            onChange={(e) => patch({ defaultDocumentType: e.target.value || null })}
            className={`${fieldInput} max-w-[16rem]`}
          >
            <option value="">{t("structure.noDefault")}</option>
            {documentDefaults.map((name) => (
              <option key={name} value={name}>
                {name}
              </option>
            ))}
          </select>
        </label>
        <label className="checkbox-label">
          <input
            type="checkbox"
            checked={fields.childrenFromTemplateOnly}
            onChange={(e) => patch({ childrenFromTemplateOnly: e.target.checked })}
            className={checkbox}
          />
          {t("structure.templateOnly")}
        </label>
      </div>
      <p className="text-sm opacity-80">{t("structure.defaultsHint")}</p>
    </section>
  );
}
