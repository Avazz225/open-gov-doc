// Dünne Wrapper-Schicht um die Office.js/Word-JS-API (3.3a, P14-S8) - hält
// jede direkte `Office`/`Word`-Berührung an einer Stelle, damit
// Komponenten testbar bleiben (Tests mocken `Office`/`Word` als globale
// Objekte, siehe tests/office-mock.ts, nicht diese Datei selbst).
//
// Diese Session deckt bewusst nur **Word** ab (Host "Document") - Excel/
// PowerPoint haben keine vergleichbare "gesamtes Dokument ersetzen"-API
// (`insertFileFromBase64` ist eine reine Word-JS-API), siehe
// docs/services/office-addin.md "Offene Punkte".

const SETTING_DOCUMENT_ID = "ogdoc.documentId";
const SETTING_VERSION_NUMBER = "ogdoc.versionNumber";

export interface LinkedDocument {
  documentId: string;
  versionNumber: number;
}

export async function waitForOfficeReady(): Promise<void> {
  await Office.onReady();
}

// `Office.context.document.settings` ist add-in-spezifischer, in der Datei
// selbst gespeicherter Schlüssel/Wert-Zustand (eigene XML-Custom-Part) - für
// GENAU diesen Zweck vorgesehen: "welches DMS-Dokument gehört zu dieser
// Word-Datei" bleibt auch nach Schließen/erneutem Öffnen bekannt, ohne einen
// eigenen Server-Zustand zu brauchen.
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

// Ersetzt den GESAMTEN Word-Dokumentinhalt durch die übergebene .docx-Datei
// (als Base64) - `Body.insertFileFromBase64` mit `InsertLocation.Replace`
// ist die dafür offiziell vorgesehene Word-JS-API (WordApi 1.5), kein
// Workaround über Copy/Paste oder Ähnliches.
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

// Liest die Rohbytes des GERADE GEÖFFNETEN Word-Dokuments (für "In DMS
// speichern") - `getFileAsync`/`getSliceAsync` sind Teil der plattform-
// übergreifenden Office-"Common API" (nicht Word-spezifisch), liefern die
// Datei in Segmenten (`sliceSize`), die hier sequenziell eingesammelt und
// zu einer einzigen Base64-Zeichenkette zusammengesetzt werden.
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
      /* Rückgabewert absichtlich ignoriert - ein fehlgeschlagenes Schließen
         des internen Datei-Handles ist nicht behebbar und blockiert nicht
         den eigentlichen Speichervorgang. */
    });
  }
}

export function base64ToBlob(base64: string, contentType: string): Blob {
  const binary = atob(base64);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
  return new Blob([bytes], { type: contentType });
}

// Umkehrung von `base64ToBlob` - für Dokumentinhalte, die als `Blob` vom DMS
// heruntergeladen wurden (`fetch().blob()`) und per `insertFileFromBase64`
// ins Word-Dokument geladen werden sollen.
export async function blobToBase64(blob: Blob): Promise<string> {
  const buffer = await blob.arrayBuffer();
  return bytesToBase64(Array.from(new Uint8Array(buffer)));
}
