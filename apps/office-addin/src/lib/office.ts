// Thin wrapper layer around the Office.js/Word JS API (3.3a, P14-S8) - keeps
// every direct `Office`/`Word` touchpoint in one place so that
// components remain testable (tests mock `Office`/`Word` as global
// objects, see tests/office-mock.ts, not this file itself).
//
// This session deliberately covers only **Word** (host "Document") - Excel/
// PowerPoint have no comparable "replace entire document" API
// (`insertFileFromBase64` is a Word-JS-only API), see
// docs/services/office-addin.md "Open Points".

const SETTING_DOCUMENT_ID = "ogdoc.documentId";
const SETTING_VERSION_NUMBER = "ogdoc.versionNumber";

export interface LinkedDocument {
  documentId: string;
  versionNumber: number;
}

export async function waitForOfficeReady(): Promise<void> {
  await Office.onReady();
}

// `Office.context.document.settings` is add-in-specific key/value state
// stored inside the file itself (its own XML custom part) - designed for
// EXACTLY this purpose: "which DMS document belongs to this
// Word file" remains known even after closing/reopening, without needing
// dedicated server-side state.
export function getLinkedDocument(): LinkedDocument | null {
  const settings = Office.context.document.settings;
  const documentId = settings.get(SETTING_DOCUMENT_ID) as string | undefined;
  const versionNumber = settings.get(SETTING_VERSION_NUMBER) as number | undefined;
  if (!documentId || versionNumber === undefined || versionNumber === null) return null;
  return { documentId, versionNumber };
}

function saveSettings(): Promise<void> {
  return new Promise((resolve, reject) => {
    Office.context.document.settings.saveAsync((result) => {
      if (result.status === Office.AsyncResultStatus.Succeeded) resolve();
      else reject(result.error);
    });
  });
}

export async function setLinkedDocument(documentId: string, versionNumber: number): Promise<void> {
  const settings = Office.context.document.settings;
  settings.set(SETTING_DOCUMENT_ID, documentId);
  settings.set(SETTING_VERSION_NUMBER, versionNumber);
  await saveSettings();
}

export async function clearLinkedDocument(): Promise<void> {
  const settings = Office.context.document.settings;
  settings.remove(SETTING_DOCUMENT_ID);
  settings.remove(SETTING_VERSION_NUMBER);
  await saveSettings();
}

// Replaces the ENTIRE Word document content with the given .docx file
// (as Base64) - `Body.insertFileFromBase64` with `InsertLocation.Replace`
// is the officially intended Word-JS API for this (WordApi 1.5), not a
// workaround via copy/paste or similar.
export async function replaceDocumentContentFromBase64(base64: string): Promise<void> {
  await Word.run(async (context) => {
    context.document.body.insertFileFromBase64(base64, Word.InsertLocation.replace);
    await context.sync();
  });
}

function bytesToBase64(bytes: number[]): string {
  let binary = "";
  for (const byte of bytes) binary += String.fromCharCode(byte);
  return btoa(binary);
}

// Reads the raw bytes of the CURRENTLY OPEN Word document (for "Save to
// DMS") - `getFileAsync`/`getSliceAsync` are part of the cross-platform
// Office "Common API" (not Word-specific); they deliver the file
// in segments (`sliceSize`), which are collected sequentially here and
// assembled into a single Base64 string.
export async function getCurrentDocumentAsBase64(): Promise<string> {
  const file = await new Promise<Office.File>((resolve, reject) => {
    Office.context.document.getFileAsync(
      Office.FileType.Compressed,
      { sliceSize: 65536 },
      (result) => {
        if (result.status === Office.AsyncResultStatus.Succeeded) resolve(result.value);
        else reject(result.error);
      }
    );
  });

  try {
    const chunks: number[][] = [];
    for (let i = 0; i < file.sliceCount; i++) {
      const slice = await new Promise<Office.Slice>((resolve, reject) => {
        file.getSliceAsync(i, (result) => {
          if (result.status === Office.AsyncResultStatus.Succeeded) resolve(result.value);
          else reject(result.error);
        });
      });
      chunks.push(Array.from(slice.data as ArrayLike<number>));
    }
    return bytesToBase64(chunks.flat());
  } finally {
    file.closeAsync(() => {
      /* Return value intentionally ignored - a failed close
         of the internal file handle is not recoverable and does not block
         the actual save operation. */
    });
  }
}

export function base64ToBlob(base64: string, contentType: string): Blob {
  const binary = atob(base64);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
  return new Blob([bytes], { type: contentType });
}

// Inverse of `base64ToBlob` - for document content that was downloaded
// as a `Blob` from the DMS (`fetch().blob()`) and is to be loaded
// into the Word document via `insertFileFromBase64`.
export async function blobToBase64(blob: Blob): Promise<string> {
  const buffer = await blob.arrayBuffer();
  return bytesToBase64(Array.from(new Uint8Array(buffer)));
}
