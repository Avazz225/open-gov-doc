"use client";

import { useCallback, useEffect, useState, type FormEvent } from "react";
import { useI18n } from "@/i18n";
import {
  ApiError,
  type ObjectType,
  createObjectType,
  deleteObjectType,
  listObjectTypes,
} from "@/lib/api";
import { useAuth } from "@/lib/auth-context";

const EXAMPLE_ATTRIBUTES = `[{"name": "Rechnungsnummer", "required": true}]`;

export function ObjectTypeEditor() {
  const { accessToken } = useAuth();
  const { t } = useI18n();
  const [objectTypes, setObjectTypes] = useState<ObjectType[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [name, setName] = useState("");
  const [appliesTo, setAppliesTo] = useState<"document" | "folder">("document");
  const [attributesJson, setAttributesJson] = useState("[]");

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

  async function handleCreate(event: FormEvent) {
    event.preventDefault();
    if (!accessToken) return;
    setError(null);
    let attributes;
    try {
      attributes = JSON.parse(attributesJson || "[]");
    } catch {
      setError(t("objectTypes.invalidJson"));
      return;
    }
    try {
      await createObjectType(accessToken, { name, appliesTo, attributes });
      setName("");
      setAttributesJson("[]");
      await reload();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("objectTypes.createError"));
    }
  }

  async function handleDelete(id: number) {
    if (!accessToken) return;
    try {
      await deleteObjectType(accessToken, id);
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
        <h2>{t("objectTypes.newHeading")}</h2>
        <form aria-label={t("objectTypes.formLabel")} onSubmit={handleCreate}>
          <div className="form-grid">
            <label>
              {t("objectTypes.name")}
              <input value={name} onChange={(e) => setName(e.target.value)} required />
            </label>
            <label>
              {t("objectTypes.appliesTo")}
              <select
                value={appliesTo}
                onChange={(e) => setAppliesTo(e.target.value as "document" | "folder")}
              >
                <option value="document">{t("objectTypes.appliesToDocument")}</option>
                <option value="folder">{t("objectTypes.appliesToFolder")}</option>
              </select>
            </label>
          </div>
          <label>
            {t("objectTypes.attributesLabel")}
            <textarea
              rows={4}
              style={{ width: "100%", fontFamily: "monospace" }}
              value={attributesJson}
              onChange={(e) => setAttributesJson(e.target.value)}
              placeholder={EXAMPLE_ATTRIBUTES}
            />
          </label>
          <button type="submit">{t("common.create")}</button>
        </form>
      </section>

      <table className="data-table">
        <thead>
          <tr>
            <th>{t("objectTypes.nameColumn")}</th>
            <th>{t("objectTypes.appliesToColumn")}</th>
            <th>{t("objectTypes.attributesColumn")}</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {objectTypes.map((ot) => (
            <tr key={ot.id}>
              <td>{ot.name}</td>
              <td>{ot.applies_to}</td>
              <td>{ot.attributes.length}</td>
              <td>
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
