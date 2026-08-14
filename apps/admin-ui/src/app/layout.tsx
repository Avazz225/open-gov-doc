import type { Metadata } from "next";
import type { ReactNode } from "react";
import { I18nProvider } from "@/i18n";
import de from "@/i18n/de.json";
import { AuthProvider } from "@/lib/auth-context";
import { InstallationProvider } from "@/lib/installation-context";
import { ThemeProvider } from "@/lib/theme-context";
import "./globals.css";

// Direct JSON import instead of going through `@/i18n` (which is marked
// "use client" there) - Next.js evaluates `metadata` server-side/at build
// time, which is incompatible with a re-export from a client module.
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
