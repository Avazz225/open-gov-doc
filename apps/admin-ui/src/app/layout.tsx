import type { Metadata } from "next";
import type { ReactNode } from "react";
import { I18nProvider } from "@/i18n";
import de from "@/i18n/de.json";
import { AuthProvider } from "@/lib/auth-context";
import { InstallationProvider } from "@/lib/installation-context";
import { ThemeProvider } from "@/lib/theme-context";
import "./globals.css";

// Direkter JSON-Import statt über `@/i18n` (dort mit "use client" markiert) -
// Next.js wertet `metadata` serverseitig/zur Build-Zeit aus, das verträgt
// sich nicht mit einem Re-Export aus einem Client-Modul.
export const metadata: Metadata = {
  title: de.meta.title,
  description: de.meta.description,
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="de">
      <body>
        <I18nProvider>
          <InstallationProvider>
            <AuthProvider>
              <ThemeProvider>{children}</ThemeProvider>
            </AuthProvider>
          </InstallationProvider>
        </I18nProvider>
      </body>
    </html>
  );
}
