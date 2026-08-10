import type { Metadata } from "next";
import Script from "next/script";
import type { ReactNode } from "react";
import { OfficeGate } from "@/components/OfficeGate";
import { I18nProvider } from "@/i18n";
import de from "@/i18n/de.json";
import { AuthProvider } from "@/lib/auth-context";
import "./globals.css";

// Direkter JSON-Import statt über `@/i18n` (dort mit "use client" markiert) -
// Next.js wertet `metadata` serverseitig/zur Build-Zeit aus, gleiches Muster
// wie bei den übrigen Apps.
export const metadata: Metadata = {
  title: de.meta.title,
  description: de.meta.description,
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="de">
      <head>
        {/* Muss die von Microsoft gehostete Version sein (nicht per npm
            gebündelt) - office.js prüft zur Laufzeit gegen den tatsächlich
            ausführenden Office-Client, ein lokal mitgeliefertes Bundle wäre
            potenziell veraltet/inkompatibel. `beforeInteractive` platziert
            das Script direkt im <head> der exportierten HTML-Datei (mit
            statischem Export kompatibel) und lädt es, bevor React
            hydratisiert - `OfficeGate` wartet zusätzlich explizit auf
            `Office.onReady()`, bevor irgendein `Office`/`Word`-Aufruf
            passiert. */}
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
