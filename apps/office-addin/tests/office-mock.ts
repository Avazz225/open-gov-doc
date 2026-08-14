import { vi } from "vitest";

// Hand-written fake of the `Office`/`Word` global objects - this
// environment has no real Office host (no Windows/Office/valid
// M365 sideloading tenant), so no real `office.js` can be loaded.
// Covers only the subset that `src/lib/office.ts` actually
// touches.

export interface OfficeMockState {
  settingsStore: Record<string, unknown>;
  savedSettings: boolean;
  lastInsertedBase64: string | null;
  fileSlices: number[][];
}

export function installOfficeMock(): OfficeMockState {
  const state: OfficeMockState = {
    settingsStore: {},
    savedSettings: false,
    lastInsertedBase64: null,
    fileSlices: [[1, 2, 3]],
  };

  const AsyncResultStatus = { Succeeded: "succeeded", Failed: "failed" } as const;

  const settings = {
    get: (key: string) => state.settingsStore[key],
    set: (key: string, value: unknown) => {
      state.settingsStore[key] = value;
    },
    remove: (key: string) => {
      delete state.settingsStore[key];
    },
    saveAsync: (callback: (result: { status: string; error?: unknown }) => void) => {
      state.savedSettings = true;
      callback({ status: AsyncResultStatus.Succeeded });
    },
  };

  const document = {
    settings,
    getFileAsync: (
      _fileType: unknown,
      _options: unknown,
      callback: (result: { status: string; value?: unknown; error?: unknown }) => void
    ) => {
      const sliceCount = state.fileSlices.length;
      const file = {
        sliceCount,
        getSliceAsync: (
          index: number,
          sliceCallback: (result: { status: string; value?: unknown; error?: unknown }) => void
        ) => {
          sliceCallback({
            status: AsyncResultStatus.Succeeded,
            value: { data: state.fileSlices[index] },
          });
        },
        closeAsync: (closeCallback: (result: { status: string }) => void) =>
          closeCallback({ status: AsyncResultStatus.Succeeded }),
      };
      callback({ status: AsyncResultStatus.Succeeded, value: file });
    },
  };

  (globalThis as Record<string, unknown>).Office = {
    onReady: vi.fn().mockResolvedValue({ host: "Word", platform: "PC" }),
    context: { document },
    AsyncResultStatus,
    FileType: { Compressed: "compressed" },
  };

  (globalThis as Record<string, unknown>).Word = {
    InsertLocation: { replace: "Replace" },
    run: vi.fn(async (callback: (context: unknown) => Promise<void>) => {
      const context = {
        document: {
          body: {
            insertFileFromBase64: (base64: string) => {
              state.lastInsertedBase64 = base64;
            },
          },
        },
        sync: vi.fn().mockResolvedValue(undefined),
      };
      await callback(context);
    }),
  };

  return state;
}
