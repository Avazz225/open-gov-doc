import type { DocumentSummary, ObjectType } from "./api";

// Reservierter Attributschlüssel, den der Document Service bei konfiguriertem
// Kennzeichengenerator serverseitig vergibt (2.2, P5e-S2) - dieselbe Konstante
// wie `KENNZEICHEN_ATTRIBUTE` im Document Service.
export const KENNZEICHEN_ATTRIBUTE = "Kennzeichen";

/**
 * Löst auf, ob und mit welchem Wert das Kennzeichen eines Dokuments vor dem
 * Dateinamen angezeigt werden soll (2.2/8, P5e-S3): der Tri-State-Override
 * der Dokumentenart gewinnt, falls gesetzt, sonst gilt der globale Standard
 * aus `KennzeichenConfig`. `null` heißt: nichts anzeigen (kein Kennzeichen
 * vergeben oder Anzeige deaktiviert).
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

/** Dateiname mit vorangestelltem Kennzeichen, falls die Anzeige aktiv ist. */
export function formatDocumentTitle(
  doc: Pick<DocumentSummary, "title" | "attributes" | "object_type_id">,
  objectTypeById: Record<number, ObjectType>,
  globalShowByDefault: boolean
): string {
  const kennzeichen = resolveKennzeichenDisplay(doc, objectTypeById, globalShowByDefault);
  return kennzeichen ? `${kennzeichen} ${doc.title}` : doc.title;
}
