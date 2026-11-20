// Display helpers for values that are data, not prose (P83-S11, craft-lens
// punch list item 6 - "no raw values as content").

// A UUID, optionally with a suffix: OCR/extraction tasks key their instance
// as `<document id>:<version>` (seen at 768px in P86-S2, where the full key
// wrapped over three lines).
const UUID_KEY = /^([0-9a-f]{8})-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}(:.+)?$/i;

/** A related-object key: a UUID shows short and monospaced with the full
 *  value on hover (the same treatment migration-console gives its ids), a
 *  `<uuid>:<suffix>` key keeps its suffix (`60ccb725:2`); anything else - a
 *  readable business key such as "Rechnung-2026-17" - is shown as it is. */
export function ShortKey({ value }: { value: string | null }) {
  if (!value) return <>-</>;
  const match = UUID_KEY.exec(value);
  if (!match) return <>{value}</>;
  return (
    <code className="font-mono text-xs opacity-80 whitespace-nowrap" title={value}>
      {match[1]}
      {match[2] ?? ""}
    </code>
  );
}

/** A four-eyes action type (`document.force_unlock`) as a translated label.
 *  The i18n lookup is nested by dots, so the type itself is the key path
 *  under `actionTypes`. An unknown type - every service may add new ones,
 *  query-service's manipulation actions are even configurable - falls back
 *  to the raw value, which the caller shows monospaced. */
export function actionTypeLabel(
  t: (path: string) => string,
  actionType: string
): { label: string; known: boolean } {
  const key = `actionTypes.${actionType}`;
  const label = t(key);
  return label === key ? { label: actionType, known: false } : { label, known: true };
}
