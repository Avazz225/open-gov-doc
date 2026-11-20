import type { Metadata } from "next";
import type { ReactNode } from "react";
import { OfficeGate } from "@/components/OfficeGate";
import de from "@/i18n/de.json";
import { AuthProvider } from "@/lib/auth-context";
import { LocaleProvider } from "@/lib/locale-context";
import "./globals.css";

// Direct JSON import instead of via `@/i18n` (marked with "use client"
// there) - Next.js evaluates `metadata` server-side/at build time, same
// pattern as in the other apps.
export const metadata: Metadata = {
  title: de.meta.title,
  description: de.meta.description,
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="de">
      <head>
        {/* Must be the Microsoft-hosted version (not bundled via npm) -
            office.js checks at runtime against the Office client actually
            running; a locally bundled version could potentially be
            outdated/incompatible. `OfficeGate` waits explicitly for
            `Office.onReady()` before any `Office`/`Word` call happens.

            P83-S5: office.js sets `window.history.replaceState`/`pushState`
            to null (a known office.js behavior), which crashed the Next.js
            router on load with "replaceState is not a function" - the
            "Application error" outside Word, found live. The methods are
            saved before office.js runs and restored right after it. All
            three are plain, synchronous <script> tags (not next/script),
            so the HTML parser runs them strictly in this order - which is
            also how Microsoft recommends loading office.js. */}
        <script
          dangerouslySetInnerHTML={{
            __html:
              "window.__dmsHistory={replaceState:window.history.replaceState,pushState:window.history.pushState};",
          }}
        />
        {/* eslint-disable-next-line @next/next/no-sync-scripts -- office.js must load synchronously before the restore below */}
        <script src="https://appsforoffice.microsoft.com/lib/1/hosted/office.js" />
        <script
          dangerouslySetInnerHTML={{
            __html:
              "if(typeof window.history.replaceState!=='function'){window.history.replaceState=window.__dmsHistory.replaceState;}" +
              "if(typeof window.history.pushState!=='function'){window.history.pushState=window.__dmsHistory.pushState;}",
          }}
        />
      </head>
      <body>
        <LocaleProvider>
          <OfficeGate>
            <AuthProvider>{children}</AuthProvider>
          </OfficeGate>
        </LocaleProvider>
      </body>
    </html>
  );
}
