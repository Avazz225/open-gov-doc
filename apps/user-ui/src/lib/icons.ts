// Class icons for folder classes (2.2a/2.2b, since P5b-S4). Same curated
// seven-icon set as in the admin-UI object-type editor (P5b-S3) -
// deliberately duplicated rather than shared (ADR 0006: no shared business
// logic between independently deployable frontend apps). Document classes
// can never carry an icon server-side (object-type-service allows `icon`
// only for `applies_to="folder"`), so documents always show the fixed 📄.
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
