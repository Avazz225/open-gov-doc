import type { Metadata } from "next";
import type { ReactNode } from "react";
import de from "@/i18n/de.json";
import { AuthProvider } from "@/lib/auth-context";
import { LocaleProvider } from "@/lib/locale-context";
import { ThemeProvider } from "@/lib/theme-context";
import "./globals.css";

// Direct JSON import instead of via `@/i18n` (which is marked "use client") -
// Next.js evaluates `metadata` server-side/at build time, which is not
// compatible with a re-export from a client module.
export const metadata: Metadata = {
  title: de.meta.title,
  description: de.meta.description,
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="de">
      <body>
        <AuthProvider>
          <LocaleProvider>
            <ThemeProvider>{children}</ThemeProvider>
          </LocaleProvider>
        </AuthProvider>
      </body>
    </html>
  );
}
