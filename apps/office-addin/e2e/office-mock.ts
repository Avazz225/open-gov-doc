import type { Page } from "@playwright/test";

// office-addin is a Word task-pane add-in (Office.js) - `window.Office`
// does not exist at all in a plain browser (no real Word host), so
// `OfficeGate` (src/components/OfficeGate.tsx) would hang on its loading
// state forever. This installs just enough of a fake `Office` global via
// `page.addInitScript()` (runs before ANY of the page's own scripts, both
// inline and externally loaded ones - a Playwright/CDP guarantee, not a
// load-order coincidence) for `waitForOfficeReady()`
// (src/lib/office.ts - literally just `await Office.onReady()`) to
// resolve, and for the one other Office call made unconditionally right
// after login (`getLinkedDocument()` reads
// `Office.context.document.settings`, see TaskPane.tsx's mount effect).
//
// Deliberately NOT reused from tests/office-mock.ts (the equivalent fake
// for the Vitest component tests): that file mocks `Word.run` and
// `getFileAsync` too, needed there to unit-test actual document
// read/write. Nothing in the flow this suite covers (OfficeGate -> login
// -> task-pane shell) reaches those calls, and `addInitScript`'s callback
// is serialized and run inside the browser - it can't import from this
// project's source tree anyway, so keeping the mock minimal and
// self-contained here is the right shape, not a shortcut.
export async function installOfficeMock(page: Page): Promise<void> {
  await page.addInitScript(() => {
    const succeeded = "succeeded";
    const settingsStore: Record<string, unknown> = {};
    const settings = {
      get: (key: string) => settingsStore[key],
      set: (key: string, value: unknown) => {
        settingsStore[key] = value;
      },
      remove: (key: string) => {
        delete settingsStore[key];
      },
      saveAsync: (callback: (result: { status: string }) => void) => {
        callback({ status: succeeded });
      },
    };

    (window as unknown as Record<string, unknown>).Office = {
      onReady: () => Promise.resolve({ host: "Word", platform: "PC" }),
      context: { document: { settings } },
      AsyncResultStatus: { Succeeded: succeeded, Failed: "failed" },
      FileType: { Compressed: "compressed" },
    };
  });
}

// layout.tsx loads the real, Microsoft-hosted office.js via a
// `beforeInteractive` <Script> - the ONLY external-origin script among
// this project's 6 frontend apps. This dev environment's network can
// actually reach appsforoffice.microsoft.com (verified: plain `curl`
// returns 200), so without blocking it, the real script downloads and
// executes shortly after the mock above is installed - and, being the
// real office.js, it defines its own `window.Office`, silently
// overwriting the mock mid-test. Aborting the request keeps the injected
// mock in place for the test's whole lifetime; Next's <Script> has no
// onError handler here, so the aborted load fails silently and does not
// otherwise affect rendering.
export async function blockRealOfficeJs(page: Page): Promise<void> {
  await page.route("https://appsforoffice.microsoft.com/**", (route) => route.abort());
}
