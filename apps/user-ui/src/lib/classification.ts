// Classification level (14.2, post-roadmap phase 31 session 3, ADR 0114) -
// mirrors `document_service.repository.CLASSIFICATION_RANK`/
// `ClassificationLevelUpdate`. Kept as pure, testable functions (same
// rationale as `kennzeichen.ts`: the same resolution is needed in more than
// one component).

export const CLASSIFICATION_LEVELS = ["VS-NfD", "VS-VERTRAULICH", "GEHEIM", "STRENG GEHEIM"] as const;

export type ClassificationLevel = (typeof CLASSIFICATION_LEVELS)[number];

/** `null` (unclassified) ranks below every named level. */
export function classificationRank(level: string | null): number {
  const index = CLASSIFICATION_LEVELS.indexOf(level as ClassificationLevel);
  return index === -1 ? 0 : index + 1;
}

/**
 * Levels a principal may raise TO from the current one - the server rejects
 * anything ranked lower (409), so the UI only ever offers same-or-higher
 * choices. Includes the current level itself (a no-op "raise", allowed).
 */
export function raisableClassificationLevels(currentLevel: string | null): ClassificationLevel[] {
  const currentRank = classificationRank(currentLevel);
  return CLASSIFICATION_LEVELS.filter((_, index) => index + 1 >= currentRank);
}
