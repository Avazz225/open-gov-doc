import type { DocumentSummary, ObjectType } from "./api";

// Reserved attribute key assigned server-side by the Document Service when a
// reference-number generator is configured (2.2, P5e-S2) - the same constant
// as `KENNZEICHEN_ATTRIBUTE` in the Document Service.
export const KENNZEICHEN_ATTRIBUTE = "Kennzeichen";

/**
 * Resolves whether and with what value a document's reference number should
 * be shown before the filename (2.2/8, P5e-S3): the document class's
 * tri-state override wins if set, otherwise the global default from
 * `KennzeichenConfig` applies. `null` means: show nothing (no reference
 * number assigned, or display disabled).
 */
export function resolveKennzeichenDisplay(
  doc: Pick<DocumentSummary, "attributes" | "object_type_id">,
  objectTypeById: Record<number, ObjectType>,
  globalShowByDefault: boolean
): string | null {
  const value = doc.attributes[KENNZEICHEN_ATTRIBUTE];
  if (typeof value !== "string" || value.length === 0) return null;

  const objectType = doc.object_type_id !== null ? objectTypeById[doc.object_type_id] : undefined;
  const override = objectType?.kennzeichen_display_override;
  const shouldShow = override === null || override === undefined ? globalShowByDefault : override;
  return shouldShow ? value : null;
}

/** Filename with the reference number prepended, if display is active. */
export function formatDocumentTitle(
  doc: Pick<DocumentSummary, "title" | "attributes" | "object_type_id">,
  objectTypeById: Record<number, ObjectType>,
  globalShowByDefault: boolean
): string {
  const kennzeichen = resolveKennzeichenDisplay(doc, objectTypeById, globalShowByDefault);
  return kennzeichen ? `${kennzeichen} ${doc.title}` : doc.title;
}
