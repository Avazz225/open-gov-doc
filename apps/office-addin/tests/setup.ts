import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";
import { installOfficeMock } from "./office-mock";

// jsdoms Blob-Polyfill implementiert `arrayBuffer()` nicht (gleiche Lücke
// wie `Blob.prototype.text` in apps/user-ui/tests/setup.ts seit P5d-S2) -
// `lib/office.ts`s `blobToBase64` braucht es, um heruntergeladene DMS-
// Dokumentinhalte für `insertFileFromBase64` zu kodieren. `FileReader` ist
// in jsdom vollständiger implementiert, darüber lässt sich das nachbilden.
if (typeof Blob !== "undefined" && !Blob.prototype.arrayBuffer) {
  Blob.prototype.arrayBuffer = function (this: Blob): Promise<ArrayBuffer> {
    return new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => resolve(reader.result as ArrayBuffer);
      reader.onerror = () => reject(reader.error);
      reader.readAsArrayBuffer(this);
    });
  };
}

// Kein echter Office-Host in dieser Umgebung verfügbar (kein Windows/Office/
// gültiger M365-Sideloading-Mandant) - jeder Test läuft gegen einen
// handgeschriebenen Fake von `Office`/`Word` (office-mock.ts), demselben
// Prinzip wie das Mocken von `fetch` in den übrigen Frontend-Apps dieses
// Projekts. Wird vor jedem Test frisch installiert, einzelne Tests können
// das zurückgegebene Objekt weiter anpassen/spionieren.
installOfficeMock();

afterEach(() => {
  cleanup();
});
