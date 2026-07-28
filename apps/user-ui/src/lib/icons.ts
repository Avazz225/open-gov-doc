// Klassen-Icons für Ordnerklassen (2.2a/2.2b, seit P5b-S4). Dasselbe
// kuratierte Sieben-Icon-Set wie im Admin-UI-Objekttyp-Editor (P5b-S3) -
// bewusst dupliziert statt geteilt (ADR 0006: keine gemeinsame Fachlogik
// zwischen unabhängig deploybaren Frontend-Apps). Dokumentklassen können
// serverseitig nie ein Icon tragen (object-type-service erlaubt `icon` nur
// für `applies_to="folder"`), Dokumente zeigen deshalb immer das feste 📄.
const ICON_GLYPHS: Record<string, string> = {
  folder: "📁",
  "folder-open": "📂",
  "folder-star": "⭐",
  archive: "🗄️",
  briefcase: "💼",
  invoice: "🧾",
  contract: "📄",
};

export function folderIcon(icon: string | null | undefined): string {
  if (!icon) return "📁";
  return ICON_GLYPHS[icon] ?? "📁";
}
