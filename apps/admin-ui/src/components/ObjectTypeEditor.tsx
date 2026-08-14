"use client";

import { useCallback, useEffect, useMemo, useState, type FormEvent } from "react";
import { useI18n } from "@/i18n";
import {
  ApiError,
  ROOT_PARENT_TYPE,
  createObjectType,
  deleteObjectType,
  listObjectTypes,
  putObjectTypeLayout,
  updateObjectType,
  type AttributeType,
  type ClassificationLevel,
  type LayoutRow,
  type ObjectType,
  type ObjectTypeAttribute,
} from "@/lib/api";
import { useAuth } from "@/lib/auth-context";

const ATTRIBUTE_TYPES: AttributeType[] = [
  "string",
  "decimal",
  "integer",
  "boolean",
  "date",
  "reference",
];
const CLASSIFICATION_LEVELS: ClassificationLevel[] = [
  "VS-NfD",
  "VS-VERTRAULICH",
  "GEHEIM",
  "STRENG GEHEIM",
];
const ICON_OPTIONS = [
  "folder",
  "folder-open",
  "folder-star",
  "archive",
  "briefcase",
  "invoice",
  "contract",
];
// Must match the backend's smart layout generation (2.2b,
// object_type_service.layout.COLUMNS_PER_ROW), since this only replicates
// the same packing, once at creation time, with the display names assigned
// here (see the rationale in handleSubmit).
const LAYOUT_COLUMNS_PER_ROW = 2;
const LAYOUT_PURPOSES = ["display", "search", "upload"] as const;

interface AttributeDraft {
  name: string;
  label: string;
  type: AttributeType;
  required: boolean;
  pattern: string;
  min: string;
  max: string;
}

function emptyAttribute(): AttributeDraft {
  return { name: "", label: "", type: "string", required: false, pattern: "", min: "", max: "" };
}

function toBackendAttribute(draft: AttributeDraft): ObjectTypeAttribute {
  const attribute: ObjectTypeAttribute = {
    name: draft.name.trim(),
    type: draft.type,
    required: draft.required,
  };
  if (draft.type === "string" && draft.pattern.trim()) {
    attribute.pattern = draft.pattern.trim();
  }
  if ((draft.type === "decimal" || draft.type === "integer") && draft.min.trim() !== "") {
    attribute.min = Number(draft.min);
  }
  if ((draft.type === "decimal" || draft.type === "integer") && draft.max.trim() !== "") {
    attribute.max = Number(draft.max);
  }
  return attribute;
}

function buildGeneratedLayoutRows(drafts: AttributeDraft[]): LayoutRow[] {
  const rows: LayoutRow[] = [];
  for (let i = 0; i < drafts.length; i += LAYOUT_COLUMNS_PER_ROW) {
    const chunk = drafts.slice(i, i + LAYOUT_COLUMNS_PER_ROW);
    rows.push({
      columns: chunk.map((draft) => ({
        attribute: draft.name.trim(),
        label: draft.label.trim() || draft.name.trim(),
        required: draft.required,
      })),
    });
  }
  return rows;
}

function iconLabel(t: (path: string) => string, value: string): string {
  const translated = t(`objectTypes.icons.${value}`);
  return translated === `objectTypes.icons.${value}` ? value : translated;
}

export function ObjectTypeEditor() {
  const { accessToken } = useAuth();
  const { t } = useI18n();
  const [objectTypes, setObjectTypes] = useState<ObjectType[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [editingId, setEditingId] = useState<number | null>(null);
  const [name, setName] = useState("");
  const [appliesTo, setAppliesTo] = useState<"document" | "folder">("document");
  const [attributes, setAttributes] = useState<AttributeDraft[]>([]);
  const [icon, setIcon] = useState("");
  const [allowedParentTypes, setAllowedParentTypes] = useState<Set<string>>(new Set());
  const [kennzeichenFormat, setKennzeichenFormat] = useState("");
  // "default" = no override (null), otherwise "true"/"false" (tri-state, P5e-S1).
  const [kennzeichenDisplayOverride, setKennzeichenDisplayOverride] = useState<
    "default" | "true" | "false"
  >("default");
  // Minimum signature level (3.10, since P6-S7) - "none" = no requirement.
  const [requiredSignatureLevel, setRequiredSignatureLevel] = useState<
    "none" | "ses" | "aes" | "qes"
  >("none");
  // Retention (5.2, since P7-S1) - applies to BOTH document and folder
  // classes, unlike reference number/signature above. Empty string = no
  // type default.
  const [defaultRetentionDays, setDefaultRetentionDays] = useState("");
  // Tri-state like kennzeichenDisplayOverride above.
  const [deletionReasonRequiredOverride, setDeletionReasonRequiredOverride] = useState<
    "default" | "true" | "false"
  >("default");
  // Disposal & long-term archiving (5.6, since P7-S3) - applies to both
  // appliesTo values, like defaultRetentionDays.
  const [defaultArchiveAfterDays, setDefaultArchiveAfterDays] = useState("");
  const [archiveEncryptionEnabled, setArchiveEncryptionEnabled] = useState(false);
  // Classified-material classification (2.5, since P15-S1, multi-level
  // since P17-S2, 14.2) - only valid for appliesTo="document", the same
  // client-side enforcement pattern as kennzeichenFormat/
  // requiredSignatureLevel above. "" = not classified (no
  // `ClassificationLevel` value is an empty string, so it can be used as a
  // sentinel for the empty <select> selection).
  const [classificationLevel, setClassificationLevel] = useState<ClassificationLevel | "">("");

  const reload = useCallback(async () => {
    if (!accessToken) return;
    try {
      setObjectTypes(await listObjectTypes(accessToken));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("common.loadError"));
    }
  }, [accessToken, t]);

  useEffect(() => {
    reload();
  }, [reload]);

  // Only existing folder classes may be referenced (2.2a, only folders can
  // be parent objects) - the class being edited itself is hidden (a
  // self-reference would be structurally meaningless, even though the
  // backend wouldn't throw an error, due to a lack of cycle detection,
  // ADR 0013).
  const folderTypeNames = useMemo(
    () =>
      objectTypes
        .filter((ot) => ot.applies_to === "folder" && ot.id !== editingId)
        .map((ot) => ot.name),
    [objectTypes, editingId]
  );

  function resetForm() {
    setEditingId(null);
    setName("");
    setAppliesTo("document");
    setAttributes([]);
    setIcon("");
    setAllowedParentTypes(new Set());
    setKennzeichenFormat("");
    setKennzeichenDisplayOverride("default");
    setRequiredSignatureLevel("none");
    setDefaultRetentionDays("");
    setDeletionReasonRequiredOverride("default");
    setDefaultArchiveAfterDays("");
    setArchiveEncryptionEnabled(false);
    setClassificationLevel("");
  }

  function startEdit(ot: ObjectType) {
    setEditingId(ot.id);
    setName(ot.name);
    setAppliesTo(ot.applies_to as "document" | "folder");
    setAttributes(
      ot.attributes.map((a) => ({
        name: a.name,
        label: "",
        type: a.type,
        required: Boolean(a.required),
        pattern: a.pattern ?? "",
        min: a.min !== undefined ? String(a.min) : "",
        max: a.max !== undefined ? String(a.max) : "",
      }))
    );
    setIcon(ot.icon ?? "");
    setAllowedParentTypes(new Set(ot.allowed_parent_types ?? []));
    setKennzeichenFormat(ot.kennzeichen_format ?? "");
    setKennzeichenDisplayOverride(
      ot.kennzeichen_display_override === null
        ? "default"
        : ot.kennzeichen_display_override
          ? "true"
          : "false"
    );
    setRequiredSignatureLevel(ot.required_signature_level ?? "none");
    setDefaultRetentionDays(
      ot.default_retention_days === null ? "" : String(ot.default_retention_days)
    );
    setDeletionReasonRequiredOverride(
      ot.deletion_reason_required_override === null
        ? "default"
        : ot.deletion_reason_required_override
          ? "true"
          : "false"
    );
    setDefaultArchiveAfterDays(
      ot.default_archive_after_days === null ? "" : String(ot.default_archive_after_days)
    );
    setArchiveEncryptionEnabled(ot.archive_encryption_enabled);
    setClassificationLevel(ot.classification_level ?? "");
    setError(null);
  }

  function addAttribute() {
    setAttributes((prev) => [...prev, emptyAttribute()]);
  }

  function updateAttribute(index: number, patch: Partial<AttributeDraft>) {
    setAttributes((prev) => prev.map((a, i) => (i === index ? { ...a, ...patch } : a)));
  }

  function removeAttribute(index: number) {
    setAttributes((prev) => prev.filter((_, i) => i !== index));
  }

  function toggleAllowedParentType(value: string) {
    setAllowedParentTypes((prev) => {
      const next = new Set(prev);
      if (next.has(value)) next.delete(value);
      else next.add(value);
      return next;
    });
  }

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    if (!accessToken) return;
    setError(null);

    if (attributes.some((a) => !a.name.trim())) {
      setError(t("objectTypes.invalidAttributeName"));
      return;
    }

    const backendAttributes = attributes.map(toBackendAttribute);
    const allowedParentTypesArray = allowedParentTypes.size > 0 ? Array.from(allowedParentTypes) : null;
    const iconValue = appliesTo === "folder" && icon ? icon : null;
    // The reference number generator (2.2, P5e-S1/S3) is only valid for
    // document classes - forced to null analogous to iconValue above,
    // instead of leaving the 422 rejection to the backend if
    // appliesTo="folder" is selected.
    const kennzeichenFormatValue =
      appliesTo === "document" && kennzeichenFormat.trim() ? kennzeichenFormat.trim() : null;
    const kennzeichenDisplayOverrideValue =
      appliesTo === "document" && kennzeichenDisplayOverride !== "default"
        ? kennzeichenDisplayOverride === "true"
        : null;
    // Minimum signature level (3.10) is, like the reference number fields,
    // only valid for document classes - the same client-side enforcement
    // instead of risking a 422 rejection from the backend.
    const requiredSignatureLevelValue =
      appliesTo === "document" && requiredSignatureLevel !== "none" ? requiredSignatureLevel : null;
    // Retention (5.2, P7-S1) - applies to both appliesTo values, so none of
    // the above appliesTo enforcements apply here.
    const defaultRetentionDaysValue =
      defaultRetentionDays.trim() === "" ? null : Number(defaultRetentionDays);
    const deletionReasonRequiredOverrideValue =
      deletionReasonRequiredOverride !== "default"
        ? deletionReasonRequiredOverride === "true"
        : null;
    // Disposal (5.6, P7-S3) - applies to both appliesTo values, like
    // defaultRetentionDaysValue.
    const defaultArchiveAfterDaysValue =
      defaultArchiveAfterDays.trim() === "" ? null : Number(defaultArchiveAfterDays);
    // Classified-material classification (2.5, P15-S1, multi-level since
    // P17-S2) is, like the reference number fields, only valid for document
    // classes.
    const classificationLevelValue =
      appliesTo === "document" && classificationLevel !== "" ? classificationLevel : null;

    try {
      if (editingId === null) {
        const created = await createObjectType(accessToken, {
          name,
          appliesTo,
          attributes: backendAttributes,
          allowedParentTypes: allowedParentTypesArray,
          icon: iconValue,
          kennzeichenFormat: kennzeichenFormatValue,
          kennzeichenDisplayOverride: kennzeichenDisplayOverrideValue,
          requiredSignatureLevel: requiredSignatureLevelValue,
          defaultRetentionDays: defaultRetentionDaysValue,
          deletionReasonRequiredOverride: deletionReasonRequiredOverrideValue,
          defaultArchiveAfterDays: defaultArchiveAfterDaysValue,
          archiveEncryptionEnabled,
          classificationLevel: classificationLevelValue,
        });

        // Display names live in the layout, not in the attribute schema
        // (ADR 0014) - only at creation time is an initial smart layout
        // persisted from them, with the labels assigned here, for all three
        // purposes; without deviating labels, it stays with the
        // server-generated default (no unnecessary override). Later
        // adjustments run exclusively through the separate layout designer.
        const labelsAssigned = attributes.some(
          (a) => a.label.trim() && a.label.trim() !== a.name.trim()
        );
        if (labelsAssigned) {
          const rows = buildGeneratedLayoutRows(attributes);
          for (const purpose of LAYOUT_PURPOSES) {
            await putObjectTypeLayout(accessToken, created.id, purpose, {
              rows,
              responsiveBreakpointPx: 600,
            });
          }
        }
      } else {
        const current = objectTypes.find((ot) => ot.id === editingId);
        await updateObjectType(accessToken, editingId, {
          attributes: backendAttributes,
          namingConstraints: current?.naming_constraints ?? null,
          conditions: current?.conditions ?? [],
          allowedParentTypes: allowedParentTypesArray,
          icon: iconValue,
          kennzeichenFormat: kennzeichenFormatValue,
          kennzeichenDisplayOverride: kennzeichenDisplayOverrideValue,
          requiredSignatureLevel: requiredSignatureLevelValue,
          defaultRetentionDays: defaultRetentionDaysValue,
          deletionReasonRequiredOverride: deletionReasonRequiredOverrideValue,
          defaultArchiveAfterDays: defaultArchiveAfterDaysValue,
          archiveEncryptionEnabled,
          classificationLevel: classificationLevelValue,
        });
      }
      resetForm();
      await reload();
    } catch (err) {
      setError(
        err instanceof ApiError
          ? err.message
          : t(editingId === null ? "objectTypes.createError" : "objectTypes.updateError")
      );
    }
  }

  async function handleDelete(id: number) {
    if (!accessToken) return;
    try {
      await deleteObjectType(accessToken, id);
      if (editingId === id) resetForm();
      await reload();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("common.deleteError"));
    }
  }

  return (
    <>
      {error && (
        <p className="error-text" role="alert">
          {error}
        </p>
      )}

      <section className="card">
        <h2>{editingId === null ? t("objectTypes.newHeading") : t("objectTypes.editHeading")}</h2>
        <form
          aria-label={editingId === null ? t("objectTypes.formLabel") : t("objectTypes.editFormLabel")}
          onSubmit={handleSubmit}
        >
          <div className="form-grid">
            <label>
              {t("objectTypes.name")}
              <input
                value={name}
                onChange={(e) => setName(e.target.value)}
                disabled={editingId !== null}
                required
              />
            </label>
            <label>
              {t("objectTypes.appliesTo")}
              <select
                value={appliesTo}
                onChange={(e) => setAppliesTo(e.target.value as "document" | "folder")}
                disabled={editingId !== null}
              >
                <option value="document">{t("objectTypes.appliesToDocument")}</option>
                <option value="folder">{t("objectTypes.appliesToFolder")}</option>
              </select>
            </label>
            {appliesTo === "folder" && (
              <label>
                {t("objectTypes.iconLabel")}
                <select value={icon} onChange={(e) => setIcon(e.target.value)}>
                  <option value="">{t("objectTypes.iconNone")}</option>
                  {ICON_OPTIONS.map((value) => (
                    <option key={value} value={value}>
                      {iconLabel(t, value)}
                    </option>
                  ))}
                </select>
              </label>
            )}
            {appliesTo === "document" && (
              <>
                <label>
                  {t("objectTypes.kennzeichenFormatLabel")}
                  <input
                    value={kennzeichenFormat}
                    onChange={(e) => setKennzeichenFormat(e.target.value)}
                    placeholder="{YYYY}-{Laufende_Nummer}"
                  />
                </label>
                <label>
                  {t("objectTypes.kennzeichenDisplayOverrideLabel")}
                  <select
                    value={kennzeichenDisplayOverride}
                    onChange={(e) =>
                      setKennzeichenDisplayOverride(e.target.value as "default" | "true" | "false")
                    }
                  >
                    <option value="default">{t("objectTypes.kennzeichenDisplayOverrideDefault")}</option>
                    <option value="true">{t("objectTypes.kennzeichenDisplayOverrideShow")}</option>
                    <option value="false">{t("objectTypes.kennzeichenDisplayOverrideHide")}</option>
                  </select>
                </label>
                <label>
                  {t("objectTypes.requiredSignatureLevelLabel")}
                  <select
                    value={requiredSignatureLevel}
                    onChange={(e) =>
                      setRequiredSignatureLevel(e.target.value as "none" | "ses" | "aes" | "qes")
                    }
                  >
                    <option value="none">{t("objectTypes.requiredSignatureLevelNone")}</option>
                    <option value="ses">{t("objectTypes.requiredSignatureLevelSes")}</option>
                    <option value="aes">{t("objectTypes.requiredSignatureLevelAes")}</option>
                    <option value="qes">{t("objectTypes.requiredSignatureLevelQes")}</option>
                  </select>
                </label>
              </>
            )}
          </div>
          {appliesTo === "document" && (
            <p className="hint">{t("objectTypes.kennzeichenFormatHint")}</p>
          )}

          <div className="form-grid">
            <label>
              {t("objectTypes.defaultRetentionDaysLabel")}
              <input
                type="number"
                min="0"
                value={defaultRetentionDays}
                onChange={(e) => setDefaultRetentionDays(e.target.value)}
                placeholder={t("objectTypes.defaultRetentionDaysPlaceholder")}
              />
            </label>
            <label>
              {t("objectTypes.deletionReasonRequiredOverrideLabel")}
              <select
                value={deletionReasonRequiredOverride}
                onChange={(e) =>
                  setDeletionReasonRequiredOverride(e.target.value as "default" | "true" | "false")
                }
              >
                <option value="default">{t("objectTypes.deletionReasonRequiredOverrideDefault")}</option>
                <option value="true">{t("objectTypes.deletionReasonRequiredOverrideRequired")}</option>
                <option value="false">{t("objectTypes.deletionReasonRequiredOverrideOptional")}</option>
              </select>
            </label>
            <label>
              {t("objectTypes.defaultArchiveAfterDaysLabel")}
              <input
                type="number"
                min="0"
                value={defaultArchiveAfterDays}
                onChange={(e) => setDefaultArchiveAfterDays(e.target.value)}
                placeholder={t("objectTypes.defaultArchiveAfterDaysPlaceholder")}
              />
            </label>
            <label className="checkbox-label">
              <input
                type="checkbox"
                checked={archiveEncryptionEnabled}
                onChange={(e) => setArchiveEncryptionEnabled(e.target.checked)}
              />
              {t("objectTypes.archiveEncryptionEnabledLabel")}
            </label>
            {appliesTo === "document" && (
              <label>
                {t("objectTypes.classificationLevelLabel")}
                <select
                  value={classificationLevel}
                  onChange={(e) =>
                    setClassificationLevel(e.target.value as ClassificationLevel | "")
                  }
                >
                  <option value="">{t("objectTypes.classificationLevelNone")}</option>
                  {CLASSIFICATION_LEVELS.map((level) => (
                    <option key={level} value={level}>
                      {level}
                    </option>
                  ))}
                </select>
              </label>
            )}
          </div>
          {appliesTo === "document" && (
            <p className="hint">{t("objectTypes.classificationLevelHint")}</p>
          )}

          <h3>{t("objectTypes.attributesHeading")}</h3>
          {attributes.length === 0 && <p className="empty-state">{t("objectTypes.noAttributes")}</p>}
          {attributes.map((attribute, index) => (
            <div className="attribute-row" key={index}>
              <div className="form-grid">
                <label>
                  {t("objectTypes.attributeName")}
                  <input
                    value={attribute.name}
                    onChange={(e) => updateAttribute(index, { name: e.target.value })}
                    required
                  />
                </label>
                {editingId === null && (
                  <label>
                    {t("objectTypes.attributeLabel")}
                    <input
                      value={attribute.label}
                      onChange={(e) => updateAttribute(index, { label: e.target.value })}
                      placeholder={attribute.name}
                    />
                  </label>
                )}
                <label>
                  {t("objectTypes.attributeType")}
                  <select
                    value={attribute.type}
                    onChange={(e) => updateAttribute(index, { type: e.target.value as AttributeType })}
                  >
                    {ATTRIBUTE_TYPES.map((type) => (
                      <option key={type} value={type}>
                        {t(`objectTypes.attributeTypes.${type}`)}
                      </option>
                    ))}
                  </select>
                </label>
                <label className="checkbox-label">
                  <input
                    type="checkbox"
                    checked={attribute.required}
                    onChange={(e) => updateAttribute(index, { required: e.target.checked })}
                  />
                  {t("objectTypes.attributeRequired")}
                </label>
                {attribute.type === "string" && (
                  <label>
                    {t("objectTypes.attributePattern")}
                    <input
                      value={attribute.pattern}
                      onChange={(e) => updateAttribute(index, { pattern: e.target.value })}
                    />
                  </label>
                )}
                {(attribute.type === "decimal" || attribute.type === "integer") && (
                  <>
                    <label>
                      {t("objectTypes.attributeMin")}
                      <input
                        type="number"
                        value={attribute.min}
                        onChange={(e) => updateAttribute(index, { min: e.target.value })}
                      />
                    </label>
                    <label>
                      {t("objectTypes.attributeMax")}
                      <input
                        type="number"
                        value={attribute.max}
                        onChange={(e) => updateAttribute(index, { max: e.target.value })}
                      />
                    </label>
                  </>
                )}
              </div>
              <button type="button" onClick={() => removeAttribute(index)}>
                {t("objectTypes.removeAttribute")}
              </button>
            </div>
          ))}
          <button type="button" onClick={addAttribute}>
            {t("objectTypes.addAttribute")}
          </button>

          <h3>{t("objectTypes.allowedParentTypesLabel")}</h3>
          <p className="hint">{t("objectTypes.allowedParentTypesHint")}</p>
          <div className="checkbox-group">
            <label>
              <input
                type="checkbox"
                checked={allowedParentTypes.has(ROOT_PARENT_TYPE)}
                onChange={() => toggleAllowedParentType(ROOT_PARENT_TYPE)}
              />
              {t("objectTypes.allowedParentTypesRootOption")}
            </label>
            {folderTypeNames.map((typeName) => (
              <label key={typeName}>
                <input
                  type="checkbox"
                  checked={allowedParentTypes.has(typeName)}
                  onChange={() => toggleAllowedParentType(typeName)}
                />
                {typeName}
              </label>
            ))}
          </div>

          <div className="actions">
            <button type="submit">
              {editingId === null ? t("common.create") : t("objectTypes.save")}
            </button>
            {editingId !== null && (
              <button type="button" onClick={resetForm}>
                {t("objectTypes.cancelEdit")}
              </button>
            )}
          </div>
        </form>
      </section>

      <table className="data-table">
        <thead>
          <tr>
            <th>{t("objectTypes.nameColumn")}</th>
            <th>{t("objectTypes.appliesToColumn")}</th>
            <th>{t("objectTypes.attributesColumn")}</th>
            <th>{t("objectTypes.iconColumn")}</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {objectTypes.map((ot) => (
            <tr key={ot.id}>
              <td>{ot.name}</td>
              <td>{ot.applies_to}</td>
              <td>{ot.attributes.length}</td>
              <td>{ot.icon ? iconLabel(t, ot.icon) : "—"}</td>
              <td className="actions">
                <button type="button" onClick={() => startEdit(ot)}>
                  {t("objectTypes.edit")}
                </button>
                <button type="button" onClick={() => handleDelete(ot.id)}>
                  {t("common.delete")}
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {objectTypes.length === 0 && <p className="empty-state">{t("objectTypes.empty")}</p>}
    </>
  );
}
