"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { useI18n } from "@/i18n";
import {
  ApiError,
  getStructureConfig,
  listObjectTypes,
  putStructureConfig,
  type ObjectType,
  type StructureConfig,
  type StructureEnforcement,
} from "@/lib/api";
import { useAuth } from "@/lib/auth-context";
import { buildStructureTree, structureWarnings, type StructureNode } from "@/lib/structure";
import { StructureListEditor } from "./StructureListEditor";

const MODES: StructureEnforcement[] = ["off", "report", "enforce"];

// The installation-wide part of the filing structure (P91-S3, ADR 0255):
// the enforcement mode and the root's lists, plus the resulting tree of
// classes with contradictions, so an admin sees what the rules add up to.
export function StructureSettings({ onSaved }: { onSaved?: () => void }) {
  const { accessToken } = useAuth();
  const { t } = useI18n();
  const [config, setConfig] = useState<StructureConfig | null>(null);
  const [draft, setDraft] = useState<StructureConfig | null>(null);
  const [types, setTypes] = useState<ObjectType[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [status, setStatus] = useState<string | null>(null);

  const load = useCallback(async () => {
    if (!accessToken) return;
    try {
      const [loaded, objectTypes] = await Promise.all([
        getStructureConfig(accessToken),
        listObjectTypes(accessToken),
      ]);
      setConfig(loaded);
      setDraft(loaded);
      setTypes(objectTypes);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("common.loadError"));
    }
  }, [accessToken, t]);

  useEffect(() => {
    load();
  }, [load]);

  const tree = useMemo(() => (draft ? buildStructureTree(types, draft) : null), [types, draft]);
  const warnings = useMemo(() => (draft ? structureWarnings(types, draft) : []), [types, draft]);

  if (!draft || !config) {
    return error ? (
      <p className="text-danger" role="alert">
        {error}
      </p>
    ) : null;
  }

  const dirty = JSON.stringify(draft) !== JSON.stringify(config);
  const folderNames = types.filter((x) => x.applies_to === "folder").map((x) => x.name);
  const documentNames = types.filter((x) => x.applies_to === "document").map((x) => x.name);

  async function save() {
    if (!accessToken || !draft) return;
    setError(null);
    setStatus(null);
    try {
      const saved = await putStructureConfig(accessToken, {
        enforcement: draft.enforcement,
        root_allowed_folder_types: draft.root_allowed_folder_types,
        root_allowed_document_types: draft.root_allowed_document_types,
      });
      setConfig(saved);
      setDraft(saved);
      setStatus(t("structure.saved"));
      onSaved?.();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("structure.saveError"));
    }
  }

  const checkbox = "h-4 w-4 rounded border-border accent-accent";
  return (
    <section className="mb-6 rounded-lg border border-border p-4" aria-labelledby="structure-settings">
      <h2 id="structure-settings">{t("structure.settingsHeading")}</h2>
      <fieldset className="m-0 min-w-0 border-0 p-0">
        <legend className="mb-1 font-semibold">{t("structure.modeLegend")}</legend>
        <div className="flex flex-col gap-1 text-sm">
          {MODES.map((mode) => (
            <label key={mode} className="flex items-start gap-2">
              <input
                type="radio"
                name="structure-mode"
                className="mt-1"
                checked={draft.enforcement === mode}
                onChange={() => setDraft({ ...draft, enforcement: mode })}
              />
              <span>
                <strong>{t(`structure.mode.${mode}`)}</strong> – {t(`structure.modeHint.${mode}`)}
              </span>
            </label>
          ))}
        </div>
      </fieldset>

      <div className="mt-4 grid gap-3 md:grid-cols-2">
        <StructureListEditor
          legend={t("structure.rootFolders")}
          value={draft.root_allowed_folder_types}
          options={folderNames}
          onChange={(next) => setDraft({ ...draft, root_allowed_folder_types: next })}
          checkbox={checkbox}
        />
        <StructureListEditor
          legend={t("structure.rootDocuments")}
          value={draft.root_allowed_document_types}
          options={documentNames}
          onChange={(next) => setDraft({ ...draft, root_allowed_document_types: next })}
          checkbox={checkbox}
        />
      </div>

      <div className="mt-4 flex flex-wrap items-center gap-3">
        <button
          type="button"
          disabled={!dirty}
          onClick={save}
          className="rounded-md border-0 bg-accent px-3 py-1.5 text-sm text-accent-fg disabled:cursor-not-allowed disabled:opacity-50"
        >
          {t("common.save")}
        </button>
        {status && (
          <span role="status" className="text-sm">
            {status}
          </span>
        )}
      </div>
      {error && (
        <p className="text-danger" role="alert">
          {error}
        </p>
      )}

      <h3 className="mt-6">{t("structure.overviewHeading")}</h3>
      <p className="text-sm opacity-80">{t("structure.overviewHint")}</p>
      {tree && tree.children.length === 0 ? (
        <p className="text-sm italic opacity-70">{t("structure.overviewEmpty")}</p>
      ) : (
        tree && (
          <ul aria-label={t("structure.overviewHeading")} className="m-0 list-none p-0 text-sm">
            <StructureTreeNode node={tree} t={t} />
          </ul>
        )
      )}
      {warnings.length > 0 && (
        <ul aria-label={t("structure.warningsLabel")} className="mt-3 text-sm text-[var(--dms-warn)]">
          {warnings.map((w, i) => (
            <li key={i}>{t(w.key, w.vars)}</li>
          ))}
        </ul>
      )}
    </section>
  );
}

function StructureTreeNode({
  node,
  t,
}: {
  node: StructureNode;
  t: (key: string, vars?: Record<string, string | number>) => string;
}) {
  const label =
    node.kind === "root"
      ? t("structure.rootLabel")
      : node.kind === "document"
        ? `📄 ${node.name}`
        : `📁 ${node.name}${node.repeated ? ` (${t("structure.repeated")})` : ""}`;
  return (
    <li className="py-0.5">
      {label}
      {node.children.length > 0 && (
        <ul className="m-0 list-none border-l border-border pl-4">
          {node.children.map((child, i) => (
            <StructureTreeNode key={`${child.kind}-${child.name}-${i}`} node={child} t={t} />
          ))}
        </ul>
      )}
    </li>
  );
}
