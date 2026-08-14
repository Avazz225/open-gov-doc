import type { Metadata } from "next";
import Script from "next/script";
import type { ReactNode } from "react";
import { OfficeGate } from "@/components/OfficeGate";
import { I18nProvider } from "@/i18n";
import de from "@/i18n/de.json";
import { AuthProvider } from "@/lib/auth-context";
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
            outdated/incompatible. `beforeInteractive` places the script
            directly in the <head> of the exported HTML file (compatible
            with static export) and loads it before React hydrates -
            `OfficeGate` additionally waits explicitly for
            `Office.onReady()` before any `Office`/`Word` call happens. */}
        <Script
          src="https://appsforoffice.microsoft.com/lib/1/hosted/office.js"
          strategy="beforeInteractive"
        />
      </head>
      <body>
        <I18nProvider>
          <OfficeGate>
            <AuthProvider>{children}</AuthProvider>
          </OfficeGate>
        </I18nProvider>
      </body>
    </html>
  );
}
