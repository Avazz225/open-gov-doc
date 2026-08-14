import type { Metadata } from "next";
import type { ReactNode } from "react";
import { I18nProvider } from "@/i18n";
import de from "@/i18n/de.json";
import { AuthProvider } from "@/lib/auth-context";
import { ThemeProvider } from "@/lib/theme-context";
import "./globals.css";

// Direct JSON import instead of via `@/i18n` (which is marked with "use client") -
// Next.js evaluates `metadata` server-side/at build time, which is
// incompatible with a re-export from a client module.
export const metadata: Metadata = {
  title: de.meta.title,
  description: de.meta.description,
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="de">
      <body>
        <I18nProvider>
          <AuthProvider>
            <ThemeProvider>{children}</ThemeProvider>
          </AuthProvider>
        </I18nProvider>
      </body>
    </html>
  );
}
