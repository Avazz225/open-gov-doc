import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";
import { installOfficeMock } from "./office-mock";

// jsdom's Blob polyfill does not implement `arrayBuffer()` (same gap
// as `Blob.prototype.text` in apps/user-ui/tests/setup.ts since P5d-S2) -
// `lib/office.ts`'s `blobToBase64` needs it to encode downloaded DMS
// document content for `insertFileFromBase64`. `FileReader` is
// more fully implemented in jsdom, so this can be reconstructed via that.
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

// No real Office host available in this environment (no Windows/Office/
// valid M365 sideloading tenant) - every test runs against a
// hand-written fake of `Office`/`Word` (office-mock.ts), the same
// principle as mocking `fetch` in the other frontend apps of this
// project. Freshly installed before each test; individual tests can
// further adapt/spy on the returned object.
installOfficeMock();

afterEach(() => {
  cleanup();
});
