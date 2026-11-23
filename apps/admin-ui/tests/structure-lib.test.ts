import { describe, expect, it } from "vitest";
import type { ObjectType } from "@/lib/api";
import { buildStructureTree, structureFieldsError, structureWarnings } from "@/lib/structure";

// P91-S3 (ADR 0255): the overview and the form checks are pure.

function type(partial: Partial<ObjectType> & { name: string; applies_to: string }): ObjectType {
  return { id: 0, attributes: [], allowed_parent_types: null, ...partial } as ObjectType;
}

const ABTEILUNG = type({
  name: "Abteilung",
  applies_to: "folder",
  allowed_parent_types: ["$ROOT"],
  allowed_child_folder_types: ["Team"],
});
const TEAM = type({ name: "Team", applies_to: "folder", allowed_document_types: ["Akte"] });
const AKTE = type({ name: "Akte", applies_to: "document" });
const ROOT_ONLY = { root_allowed_folder_types: ["Abteilung"], root_allowed_document_types: [] };

describe("buildStructureTree", () => {
  it("follows the lists from the root down", () => {
    const tree = buildStructureTree([ABTEILUNG, TEAM, AKTE], ROOT_ONLY);
    expect(tree.children.map((n) => n.name)).toEqual(["Abteilung"]);
    const team = tree.children[0].children[0];
    expect(team.name).toBe("Team");
    expect(team.children).toEqual([{ name: "Akte", kind: "document", children: [] }]);
  });

  it("stops a class that repeats on its own path", () => {
    const nested = type({ name: "Gruppe", applies_to: "folder", allowed_child_folder_types: ["Gruppe"], allowed_parent_types: ["$ROOT", "Gruppe"] });
    const tree = buildStructureTree([nested], { root_allowed_folder_types: null, root_allowed_document_types: null });
    expect(tree.children[0].children[0]).toMatchObject({ name: "Gruppe", repeated: true, children: [] });
  });
});

describe("structureWarnings", () => {
  it("names listed children that refuse the parent, and classes placeable nowhere", () => {
    const lonely = type({ name: "Einsam", applies_to: "folder", allowed_parent_types: ["Nirgends"] });
    const warnings = structureWarnings(
      [{ ...ABTEILUNG, allowed_child_folder_types: ["Team", "Abteilung"] }, TEAM, AKTE, lonely],
      ROOT_ONLY
    );
    expect(warnings).toContainEqual({
      key: "structure.warningUnreachable",
      vars: { child: "Abteilung", parent: "Abteilung" },
    });
    expect(warnings).toContainEqual({ key: "structure.warningOrphan", vars: { name: "Einsam" } });
  });
});

describe("structureFieldsError", () => {
  it("wants a default to be in its list", () => {
    expect(
      structureFieldsError({
        allowedChildFolderTypes: ["Team"],
        allowedDocumentTypes: null,
        defaultChildFolderType: "Vorgang",
        defaultDocumentType: null,
        childrenFromTemplateOnly: false,
      })
    ).toEqual({ key: "structure.defaultFolderNotListed", vars: { name: "Vorgang" } });
  });
});
