import { beforeEach, describe, expect, it } from "vitest";
import {
  base64ToBlob,
  blobToBase64,
  clearLinkedDocument,
  getCurrentDocumentAsBase64,
  getLinkedDocument,
  replaceDocumentContentFromBase64,
  setLinkedDocument,
  waitForOfficeReady,
} from "@/lib/office";
import { installOfficeMock, type OfficeMockState } from "./office-mock";

describe("lib/office", () => {
  let state: OfficeMockState;

  beforeEach(() => {
    state = installOfficeMock();
  });

  it("resolves once Office.onReady resolves", async () => {
    await expect(waitForOfficeReady()).resolves.toBeUndefined();
  });

  it("has no linked document by default", () => {
    expect(getLinkedDocument()).toBeNull();
  });

  it("persists a linked document via settings and saves", async () => {
    await setLinkedDocument("doc-1", 3);
    expect(getLinkedDocument()).toEqual({ documentId: "doc-1", versionNumber: 3 });
    expect(state.savedSettings).toBe(true);
  });

  it("clears a linked document", async () => {
    await setLinkedDocument("doc-1", 3);
    await clearLinkedDocument();
    expect(getLinkedDocument()).toBeNull();
  });

  it("replaces the document body with the given base64 content", async () => {
    await replaceDocumentContentFromBase64("Zm9v");
    expect(state.lastInsertedBase64).toBe("Zm9v");
  });

  it("reassembles file slices into a base64 string", async () => {
    state.fileSlices = [
      [72, 101],
      [108, 108, 111],
    ]; // "He" + "llo" -> "Hello"
    const base64 = await getCurrentDocumentAsBase64();
    expect(base64).toBe(btoa("Hello"));
  });

  it("converts base64 back into a Blob with the given content type", async () => {
    const blob = base64ToBlob(btoa("Hello"), "text/plain");
    expect(blob.type).toBe("text/plain");
    const buffer = await blob.arrayBuffer();
    expect(String.fromCharCode(...new Uint8Array(buffer))).toBe("Hello");
  });

  it("round-trips a Blob through blobToBase64", async () => {
    const base64 = await blobToBase64(new Blob(["Hello"]));
    expect(base64).toBe(btoa("Hello"));
  });
});
