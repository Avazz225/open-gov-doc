"use client";

import { useI18n } from "@/i18n";
import type { AttributeType, MetadataMapping, MetadataOverwritePolicy, MetadataSource } from "@/lib/api";

// Embedded-metadata mappings (P90-S5, ADR 0258): which property of an
// uploaded file (PDF info/XMP, Office/OpenDocument core properties) fills
// which attribute of a document type, and whether it may overwrite what the
// uploader entered. The vocabulary must match object-type-service's
// `EMBEDDED_METADATA_SOURCES`; the checks below mirror its
// `_validate_metadata_mappings` so a mistake is named next to the form.

export const METADATA_SOURCES: MetadataSource[] = [
  "title",
  "subject",
  "author",
  "keywords",
  "description",
  "category",
  "language",
  "created",
  "modified",
  "last_modified_by",
  "application",
];
const DATE_SOURCES = new Set<MetadataSource>(["created", "modified"]);
// Attribute types a file property can fill: text from any source, a date
// only from a date property.
const MAPPABLE_TYPES = new Set<AttributeType>(["string", "date"]);

export interface MappingAttribute {
  name: string;
  type: AttributeType;
}

export function emptyMapping(): MetadataMapping {
  return { source: "title", attribute: "", overwrite: "if_empty" };
}

export function targetsFor(source: MetadataSource, attributes: MappingAttribute[]): MappingAttribute[] {
  return attributes.filter(
    (a) => a.name && MAPPABLE_TYPES.has(a.type) && (a.type === "string" || DATE_SOURCES.has(source))
  );
}

export function mappingsError(
  mappings: MetadataMapping[],
  attributes: MappingAttribute[]
): { key: string; vars?: Record<string, string | number> } | null {
  const byName = new Map(attributes.map((a) => [a.name, a]));
  const seen = new Set<string>();
  for (const [index, mapping] of mappings.entries()) {
    const vars = { index: index + 1, name: mapping.attribute };
    if (!mapping.attribute) return { key: "objectTypes.mappings.incomplete", vars };
    const attribute = byName.get(mapping.attribute);
    if (!attribute) return { key: "objectTypes.mappings.unknownAttribute", vars };
    if (!targetsFor(mapping.source, [attribute]).length) {
      return { key: "objectTypes.mappings.typeMismatch", vars };
    }
    if (seen.has(mapping.attribute)) return { key: "objectTypes.mappings.duplicate", vars };
    seen.add(mapping.attribute);
  }
  return null;
}

export function ObjectTypeMetadataMappings({
  mappings,
  onChange,
  attributes,
  fieldInput,
  secondaryBtn,
}: {
  mappings: MetadataMapping[];
  onChange: (next: MetadataMapping[]) => void;
  attributes: MappingAttribute[];
  fieldInput: string;
  secondaryBtn: string;
}) {
  const { t } = useI18n();

  function update(index: number, patch: Partial<MetadataMapping>) {
    onChange(mappings.map((m, i) => (i === index ? { ...m, ...patch } : m)));
  }

  return (
    <fieldset className="mt-4 rounded-md border border-border p-3">
      <legend className="px-1 font-semibold">{t("objectTypes.mappings.heading")}</legend>
      <p className="mt-0 text-sm opacity-80">{t("objectTypes.mappings.hint")}</p>
      {mappings.length === 0 && <p className="text-sm italic opacity-70">{t("objectTypes.mappings.none")}</p>}
      <ol className="m-0 flex list-none flex-col gap-2 p-0">
        {mappings.map((mapping, index) => {
          const targets = targetsFor(mapping.source, attributes);
          const currentMissing = mapping.attribute && !targets.some((a) => a.name === mapping.attribute);
          return (
            <li
              key={index}
              className="flex flex-wrap items-end gap-2 rounded-md bg-hover-bg p-2"
              aria-label={t("objectTypes.mappings.rowLabel", { index: index + 1 })}
            >
              <label className="flex min-w-0 flex-col gap-1 text-sm">
                {t("objectTypes.mappings.source")}
                <select
                  value={mapping.source}
                  onChange={(e) => update(index, { source: e.target.value as MetadataSource })}
                  className={`${fieldInput} max-w-[14rem]`}
                >
                  {METADATA_SOURCES.map((source) => (
                    <option key={source} value={source}>
                      {t(`objectTypes.mappings.sources.${source}`)}
                    </option>
                  ))}
                </select>
              </label>
              {/* Only where the row fits on one line; stacked, the arrow would dangle. */}
              <span className="hidden pb-2 sm:inline" aria-hidden="true">
                →
              </span>
              <label className="flex min-w-0 flex-col gap-1 text-sm">
                {t("objectTypes.mappings.attribute")}
                <select
                  value={mapping.attribute}
                  onChange={(e) => update(index, { attribute: e.target.value })}
                  className={`${fieldInput} max-w-[14rem]`}
                >
                  <option value="">{t("objectTypes.rules.chooseAttribute")}</option>
                  {targets.map((a) => (
                    <option key={a.name} value={a.name}>
                      {a.name}
                    </option>
                  ))}
                  {currentMissing && (
                    <option value={mapping.attribute}>
                      {t("objectTypes.rules.undeclared", { name: mapping.attribute })}
                    </option>
                  )}
                </select>
              </label>
              <label className="flex min-w-0 flex-col gap-1 text-sm">
                {t("objectTypes.mappings.overwrite")}
                <select
                  value={mapping.overwrite}
                  onChange={(e) => update(index, { overwrite: e.target.value as MetadataOverwritePolicy })}
                  className={`${fieldInput} max-w-[16rem]`}
                >
                  <option value="if_empty">{t("objectTypes.mappings.ifEmpty")}</option>
                  <option value="always">{t("objectTypes.mappings.always")}</option>
                </select>
              </label>
              <button
                type="button"
                className={secondaryBtn}
                aria-label={t("objectTypes.mappings.remove", { index: index + 1 })}
                onClick={() => onChange(mappings.filter((_, i) => i !== index))}
              >
                ×
              </button>
            </li>
          );
        })}
      </ol>
      <button type="button" className={`${secondaryBtn} mt-2`} onClick={() => onChange([...mappings, emptyMapping()])}>
        {t("objectTypes.mappings.add")}
      </button>
    </fieldset>
  );
}
