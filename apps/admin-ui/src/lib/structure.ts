// Enforced filing structure in the admin UI (P91-S3, ADR 0255): pure
// helpers for the object-type editor and the "Ablagestruktur" page. The
// rules themselves are evaluated by the backend; these only shape forms and
// draw the overview.

import type { ObjectType, StructureConfig, StructureFields } from "@/lib/api";

export const ROOT_PARENT_TYPE = "$ROOT";

export function emptyStructureFields(): StructureFields {
  return {
    allowedChildFolderTypes: null,
    allowedDocumentTypes: null,
    defaultChildFolderType: null,
    defaultDocumentType: null,
    childrenFromTemplateOnly: false,
  };
}

export function structureFieldsFromObjectType(ot: ObjectType): StructureFields {
  return {
    allowedChildFolderTypes: ot.allowed_child_folder_types ?? null,
    allowedDocumentTypes: ot.allowed_document_types ?? null,
    defaultChildFolderType: ot.default_child_folder_type ?? null,
    defaultDocumentType: ot.default_document_type ?? null,
    childrenFromTemplateOnly: Boolean(ot.children_from_template_only),
  };
}

// Mirrors object-type-service's `_validate_structure_fields`: a default must
// be admitted by its own list when one is set. An i18n key plus values.
export function structureFieldsError(
  fields: StructureFields
): { key: string; vars?: Record<string, string> } | null {
  const pairs: [string | null, string[] | null, string][] = [
    [fields.defaultChildFolderType, fields.allowedChildFolderTypes, "structure.defaultFolderNotListed"],
    [fields.defaultDocumentType, fields.allowedDocumentTypes, "structure.defaultDocumentNotListed"],
  ];
  for (const [value, list, key] of pairs) {
    if (value && list !== null && !list.includes(value)) return { key, vars: { name: value } };
  }
  return null;
}

// Both sides must agree (ADR 0255, Decision 1): the parent's list (null =
// open) and the child's own `allowed_parent_types` (empty = anywhere).
export function admits(
  parentList: string[] | null,
  child: ObjectType,
  parentName: string
): boolean {
  const ownRule = child.allowed_parent_types;
  const childSideOk = !ownRule || ownRule.length === 0 || ownRule.includes(parentName);
  const parentSideOk = parentList === null || parentList.includes(child.name);
  return childSideOk && parentSideOk;
}

export interface StructureNode {
  name: string;
  kind: "root" | "folder" | "document";
  // A class already shown higher up on this path - drawn, not expanded.
  repeated?: boolean;
  children: StructureNode[];
}

export interface StructureWarning {
  key: string;
  vars: Record<string, string>;
}

// The tree of classes from the root, following both sides, for the
// overview. Documents are leaves; a class repeating on its own path stops
// the branch. Untyped children are not drawn, an open list says so in
// the UI.
export function buildStructureTree(
  types: ObjectType[],
  config: Pick<StructureConfig, "root_allowed_folder_types" | "root_allowed_document_types">
): StructureNode {
  const folders = types.filter((t) => t.applies_to === "folder");
  const documents = types.filter((t) => t.applies_to === "document");

  function childrenOf(
    folderList: string[] | null,
    documentList: string[] | null,
    parentName: string,
    path: Set<string>
  ): StructureNode[] {
    const nodes: StructureNode[] = [];
    for (const folder of folders) {
      if (!admits(folderList, folder, parentName)) continue;
      // Below an open list only classes that name this parent explicitly are
      // drawn, otherwise every class would appear under every open folder.
      if (folderList === null && !(folder.allowed_parent_types ?? []).includes(parentName)) continue;
      if (path.has(folder.name)) {
        nodes.push({ name: folder.name, kind: "folder", repeated: true, children: [] });
        continue;
      }
      const next = new Set(path).add(folder.name);
      nodes.push({
        name: folder.name,
        kind: "folder",
        children: childrenOf(
          folder.allowed_child_folder_types ?? null,
          folder.allowed_document_types ?? null,
          folder.name,
          next
        ),
      });
    }
    for (const document of documents) {
      if (!admits(documentList, document, parentName)) continue;
      if (documentList === null && !(document.allowed_parent_types ?? []).includes(parentName)) continue;
      nodes.push({ name: document.name, kind: "document", children: [] });
    }
    return nodes;
  }

  return {
    name: "root",
    kind: "root",
    children: childrenOf(
      config.root_allowed_folder_types,
      config.root_allowed_document_types,
      ROOT_PARENT_TYPE,
      new Set()
    ),
  };
}

// Contradictions the admin should see (ADR 0255: shown, not forbidden).
export function structureWarnings(
  types: ObjectType[],
  config: Pick<StructureConfig, "root_allowed_folder_types" | "root_allowed_document_types">
): StructureWarning[] {
  const byName = new Map(types.map((t) => [t.name, t]));
  const warnings: StructureWarning[] = [];
  const parents: { name: string; folderList: string[] | null; documentList: string[] | null }[] = [
    {
      name: ROOT_PARENT_TYPE,
      folderList: config.root_allowed_folder_types,
      documentList: config.root_allowed_document_types,
    },
    ...types
      .filter((t) => t.applies_to === "folder")
      .map((t) => ({
        name: t.name,
        folderList: t.allowed_child_folder_types ?? null,
        documentList: t.allowed_document_types ?? null,
      })),
  ];
  for (const parent of parents) {
    for (const listed of [...(parent.folderList ?? []), ...(parent.documentList ?? [])]) {
      const child = byName.get(listed);
      if (child && !admits(null, child, parent.name)) {
        warnings.push({
          key: "structure.warningUnreachable",
          vars: { child: listed, parent: parent.name === ROOT_PARENT_TYPE ? "Wurzel" : parent.name },
        });
      }
    }
  }
  // A class no list and no open parent admits can't be placed anywhere.
  for (const type of types) {
    const placeable = parents.some((parent) => {
      const list = type.applies_to === "folder" ? parent.folderList : parent.documentList;
      return admits(list, type, parent.name) && (parent.name !== type.name);
    });
    if (!placeable) warnings.push({ key: "structure.warningOrphan", vars: { name: type.name } });
  }
  return warnings;
}
