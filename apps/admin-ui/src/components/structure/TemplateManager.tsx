"use client";

import { useCallback, useEffect, useState } from "react";
import { useI18n } from "@/i18n";
import {
  ApiError,
  createFolderTemplate,
  deleteFolderTemplate,
  getFolderTemplate,
  listChildFolders,
  listFolderTemplates,
  listObjectTypes,
  updateFolderTemplate,
  type FolderEntry,
  type FolderTemplateNode,
  type FolderTemplateSummary,
  type ObjectType,
} from "@/lib/api";
import { useAuth } from "@/lib/auth-context";

type Translate = (key: string, vars?: Record<string, string | number>) => string;

const fieldInput =
  "box-border rounded-md border border-border bg-bg px-2 py-1.5 text-sm text-fg outline-none focus:border-accent";
const button =
  "rounded-md border border-border bg-hover-bg px-3 py-1.5 text-sm text-fg hover:bg-accent-bg disabled:cursor-not-allowed disabled:opacity-50 whitespace-nowrap";
const primary =
  "rounded-md border-0 bg-accent px-3 py-1.5 text-sm text-accent-fg disabled:cursor-not-allowed disabled:opacity-50 whitespace-nowrap";

interface Draft {
  id: string | null;
  name: string;
  description: string;
  structure: FolderTemplateNode;
}

function emptyNode(name = ""): FolderTemplateNode {
  return { name, object_type_id: null, children: [] };
}

// Path of child indexes from the root node.
type NodePath = number[];

function updateAt(
  node: FolderTemplateNode,
  path: NodePath,
  change: (n: FolderTemplateNode) => FolderTemplateNode | null
): FolderTemplateNode | null {
  if (path.length === 0) return change(node);
  const [head, ...rest] = path;
  const children: FolderTemplateNode[] = [];
  node.children.forEach((child, index) => {
    if (index !== head) {
      children.push(child);
      return;
    }
    const updated = updateAt(child, rest, change);
    if (updated) children.push(updated);
  });
  return { ...node, children };
}

function hasEmptyName(node: FolderTemplateNode): boolean {
  return !node.name.trim() || node.children.some(hasEmptyName);
}

// Structure templates are admin-managed since P91-S2b (ADR 0255, Decision
// 5): built here node by node, or captured from an existing folder chosen in
// a picker (instead of a typed-in id). folder-service validates every save
// against the structure rules and names the offending node.
export function TemplateManager() {
  const { accessToken } = useAuth();
  const { t } = useI18n();
  const [templates, setTemplates] = useState<FolderTemplateSummary[]>([]);
  const [folderClasses, setFolderClasses] = useState<ObjectType[]>([]);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [picking, setPicking] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [status, setStatus] = useState<string | null>(null);

  const load = useCallback(async () => {
    if (!accessToken) return;
    try {
      const [list, types] = await Promise.all([
        listFolderTemplates(accessToken),
        listObjectTypes(accessToken),
      ]);
      setTemplates(list);
      setFolderClasses(types.filter((ot) => ot.applies_to === "folder"));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("common.loadError"));
    }
  }, [accessToken, t]);

  useEffect(() => {
    load();
  }, [load]);

  async function edit(id: string) {
    if (!accessToken) return;
    setError(null);
    setStatus(null);
    try {
      const template = await getFolderTemplate(accessToken, id);
      setDraft({
        id: template.id,
        name: template.name,
        description: template.description ?? "",
        structure: template.structure,
      });
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("common.loadError"));
    }
  }

  async function save() {
    if (!accessToken || !draft) return;
    setError(null);
    setStatus(null);
    if (!draft.name.trim() || hasEmptyName(draft.structure)) {
      setError(t("structure.templateNamesRequired"));
      return;
    }
    try {
      const payload = {
        name: draft.name.trim(),
        description: draft.description.trim() || null,
        structure: draft.structure,
      };
      if (draft.id) await updateFolderTemplate(accessToken, draft.id, payload);
      else await createFolderTemplate(accessToken, payload);
      setStatus(t("structure.templateSaved", { name: payload.name }));
      setDraft(null);
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("structure.saveError"));
    }
  }

  async function capture(folder: FolderEntry) {
    if (!accessToken) return;
    setPicking(false);
    setError(null);
    try {
      const created = await createFolderTemplate(accessToken, {
        name: t("structure.capturedName", { name: folder.name }),
        sourceFolderId: folder.id,
      });
      await load();
      await edit(created.id);
      setStatus(t("structure.templateCaptured", { name: folder.name }));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("structure.saveError"));
    }
  }

  async function remove(template: FolderTemplateSummary) {
    if (!accessToken) return;
    setError(null);
    try {
      await deleteFolderTemplate(accessToken, template.id);
      if (draft?.id === template.id) setDraft(null);
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("common.deleteError"));
    }
  }

  return (
    <section className="mb-6 rounded-lg border border-border p-4" aria-labelledby="structure-templates">
      <h2 id="structure-templates">{t("structure.templatesHeading")}</h2>
      <p className="text-sm opacity-80">{t("structure.templatesHint")}</p>
      {error && (
        <p className="text-danger" role="alert">
          {error}
        </p>
      )}
      {status && (
        <p className="text-sm" role="status">
          {status}
        </p>
      )}
      <div className="flex flex-wrap gap-2">
        <button
          type="button"
          className={button}
          onClick={() =>
            setDraft({ id: null, name: "", description: "", structure: emptyNode() })
          }
        >
          {t("structure.newTemplate")}
        </button>
        <button type="button" className={button} onClick={() => setPicking((v) => !v)}>
          {t("structure.captureTemplate")}
        </button>
      </div>
      {picking && accessToken && (
        <FolderPicker token={accessToken} onPick={capture} onCancel={() => setPicking(false)} t={t} />
      )}

      {templates.length === 0 ? (
        <p className="text-sm italic opacity-70">{t("structure.noTemplates")}</p>
      ) : (
        <ul className="mt-3 flex list-none flex-col gap-1 p-0" aria-label={t("structure.templatesHeading")}>
          {templates.map((template) => (
            <li key={template.id} className="flex flex-wrap items-center gap-2">
              <span className="min-w-0 flex-1 break-words">
                <strong>{template.name}</strong>
                {template.description && <span className="opacity-80"> – {template.description}</span>}
              </span>
              <button type="button" className={button} onClick={() => edit(template.id)}>
                {t("structure.edit")}
              </button>
              <button
                type="button"
                className={button}
                aria-label={t("structure.deleteTemplate", { name: template.name })}
                onClick={() => remove(template)}
              >
                {t("common.delete")}
              </button>
            </li>
          ))}
        </ul>
      )}

      {draft && (
        <form
          className="mt-4 rounded-md border border-border p-3"
          aria-label={t("structure.templateEditor")}
          onSubmit={(e) => {
            e.preventDefault();
            save();
          }}
        >
          <div className="form-grid">
            <label>
              {t("structure.templateName")}
              <input
                value={draft.name}
                onChange={(e) => setDraft({ ...draft, name: e.target.value })}
                className={fieldInput}
                required
              />
            </label>
            <label>
              {t("structure.templateDescription")}
              <input
                value={draft.description}
                onChange={(e) => setDraft({ ...draft, description: e.target.value })}
                className={fieldInput}
              />
            </label>
          </div>
          <p className="mb-1 mt-3 text-sm font-semibold">{t("structure.templateTree")}</p>
          <ul className="m-0 list-none p-0">
            <TemplateNodeEditor
              node={draft.structure}
              path={[]}
              folderClasses={folderClasses}
              onChange={(path, change) => {
                const next = updateAt(draft.structure, path, change);
                if (next) setDraft({ ...draft, structure: next });
              }}
              t={t}
            />
          </ul>
          <div className="mt-3 flex gap-2">
            <button type="submit" className={primary}>
              {t("common.save")}
            </button>
            <button type="button" className={button} onClick={() => setDraft(null)}>
              {t("common.cancel")}
            </button>
          </div>
        </form>
      )}
    </section>
  );
}

function TemplateNodeEditor({
  node,
  path,
  folderClasses,
  onChange,
  t,
}: {
  node: FolderTemplateNode;
  path: NodePath;
  folderClasses: ObjectType[];
  onChange: (path: NodePath, change: (n: FolderTemplateNode) => FolderTemplateNode | null) => void;
  t: Translate;
}) {
  const label = path.length === 0 ? t("structure.rootNode") : t("structure.node", { path: path.map((i) => i + 1).join(".") });
  return (
    <li className="py-1" aria-label={label}>
      <div className="flex flex-wrap items-end gap-2">
        <label className="flex min-w-0 flex-col gap-1 text-sm">
          {t("structure.nodeName")}
          <input
            value={node.name}
            onChange={(e) => onChange(path, (n) => ({ ...n, name: e.target.value }))}
            className={`${fieldInput} w-48 max-w-full`}
          />
        </label>
        <label className="flex min-w-0 flex-col gap-1 text-sm">
          {t("structure.nodeType")}
          <select
            value={node.object_type_id ?? ""}
            onChange={(e) =>
              onChange(path, (n) => ({
                ...n,
                object_type_id: e.target.value ? Number(e.target.value) : null,
              }))
            }
            className={`${fieldInput} max-w-[14rem]`}
          >
            <option value="">{t("structure.noType")}</option>
            {folderClasses.map((ot) => (
              <option key={ot.id} value={ot.id}>
                {ot.name}
              </option>
            ))}
          </select>
        </label>
        <button
          type="button"
          className={button}
          onClick={() => onChange(path, (n) => ({ ...n, children: [...n.children, emptyNode()] }))}
        >
          {t("structure.addChild")}
        </button>
        {path.length > 0 && (
          <button
            type="button"
            className={button}
            aria-label={t("structure.removeNode", { name: node.name || label })}
            onClick={() => onChange(path, () => null)}
          >
            ×
          </button>
        )}
      </div>
      {node.children.length > 0 && (
        <ul className="m-0 mt-1 list-none border-l border-border pl-3 sm:pl-5">
          {node.children.map((child, index) => (
            <TemplateNodeEditor
              key={index}
              node={child}
              path={[...path, index]}
              folderClasses={folderClasses}
              onChange={onChange}
              t={t}
            />
          ))}
        </ul>
      )}
    </li>
  );
}

function FolderPicker({
  token,
  onPick,
  onCancel,
  t,
}: {
  token: string;
  onPick: (folder: FolderEntry) => void;
  onCancel: () => void;
  t: Translate;
}) {
  const [trail, setTrail] = useState<FolderEntry[]>([]);
  const [children, setChildren] = useState<FolderEntry[]>([]);
  const [error, setError] = useState<string | null>(null);
  const current = trail[trail.length - 1];

  useEffect(() => {
    listChildFolders(token, current?.id ?? "root")
      .then(setChildren)
      .catch((err) => setError(err instanceof ApiError ? err.message : t("common.loadError")));
  }, [token, current, t]);

  return (
    <div className="mt-3 rounded-md border border-border p-3" role="group" aria-label={t("structure.pickerLabel")}>
      <p className="m-0 text-sm">
        {t("structure.pickerHint")}{" "}
        <button type="button" className="underline" onClick={() => setTrail([])}>
          {t("structure.rootLabel")}
        </button>
        {trail.map((folder, index) => (
          <span key={folder.id}>
            {" / "}
            <button type="button" className="underline" onClick={() => setTrail(trail.slice(0, index + 1))}>
              {folder.name}
            </button>
          </span>
        ))}
      </p>
      {error && (
        <p className="text-danger" role="alert">
          {error}
        </p>
      )}
      <ul className="mt-2 flex max-h-64 list-none flex-col gap-1 overflow-y-auto p-0">
        {children.map((folder) => (
          <li key={folder.id} className="flex flex-wrap items-center gap-2">
            <button type="button" className="min-w-0 flex-1 text-left underline" onClick={() => setTrail([...trail, folder])}>
              📁 {folder.name}
            </button>
            <button
              type="button"
              className={button}
              aria-label={t("structure.pickFolder", { name: folder.name })}
              onClick={() => onPick(folder)}
            >
              {t("structure.pick")}
            </button>
          </li>
        ))}
        {children.length === 0 && <li className="text-sm italic opacity-70">{t("structure.pickerEmpty")}</li>}
      </ul>
      <button type="button" className={`${button} mt-2`} onClick={onCancel}>
        {t("common.cancel")}
      </button>
    </div>
  );
}
