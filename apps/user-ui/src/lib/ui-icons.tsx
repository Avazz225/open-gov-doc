"use client";

import {
  Archive,
  Briefcase,
  FileText,
  Folder,
  FolderOpen,
  Receipt,
  Star,
  type LucideIcon,
  type LucideProps,
} from "lucide-react";

// One icon set for user-ui (P83-S6, punch list item 2, ADR 0237): Lucide,
// chosen deliberately - it is already a dependency since P83-S2 (the login
// illustration), the illustrations use it, and it covers every meaning the UI
// needed, in one stroke style. It replaces the emoji the UI used as icons,
// whose weight, color and rendering differed per platform.
//
// One size and one stroke everywhere (craft: one family, one weight, one
// size). Decorative icons are aria-hidden - the text beside them carries the
// meaning; an icon that carries meaning on its own (the favorite marker)
// gets role="img" and a translated name instead.

export const ICON_SIZE = 16;
export const ICON_STROKE = 1.75;

export function UiIcon({ icon: Icon, className, ...rest }: { icon: LucideIcon } & LucideProps) {
  return (
    <Icon
      size={ICON_SIZE}
      strokeWidth={ICON_STROKE}
      aria-hidden="true"
      focusable="false"
      className={`ui-icon${className ? ` ${className}` : ""}`}
      {...rest}
    />
  );
}

// Class icons for folder classes (2.2a/2.2b, since P5b-S4): the curated set
// the admin-ui object-type editor offers, keyed by the server-side `icon`
// value (unchanged - only the rendering moved from emoji to Lucide).
// Document classes never carry an icon server-side, so documents always show
// FileText.
const FOLDER_CLASS_ICONS: Record<string, LucideIcon> = {
  folder: Folder,
  "folder-open": FolderOpen,
  "folder-star": Star,
  archive: Archive,
  briefcase: Briefcase,
  invoice: Receipt,
  contract: FileText,
};

export function FolderClassIcon({ icon }: { icon: string | null | undefined }) {
  const Icon = (icon && FOLDER_CLASS_ICONS[icon]) || Folder;
  return <UiIcon icon={Icon} className="ui-icon--folder" data-folder-icon={icon ?? "folder"} />;
}

export function DocumentIcon() {
  return <UiIcon icon={FileText} className="ui-icon--document" />;
}

/** The favorite marker carries meaning on its own: a filled star with an
 *  accessible name, not a decorative glyph. */
export function FavoriteMarker({ label }: { label: string }) {
  return (
    <Star
      size={14}
      strokeWidth={ICON_STROKE}
      fill="currentColor"
      role="img"
      aria-label={label}
      focusable="false"
      className="ui-icon ui-icon--favorite"
    />
  );
}
