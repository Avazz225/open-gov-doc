# 0014 — Formular-Layouts: generierte Smart-Layouts bleiben ungespeichert, nur Overrides werden persistiert

**Status:** akzeptiert
**Kontext:** Konzept 2.2b, Session P5b-S2

## Entscheidung

Das Formular-Layout-Datenmodell (Zeilen/Spalten-Grid je Objekttyp und Verwendungszweck `display`/`search`/`upload`) wird wie folgt umgesetzt:

- Eine neue Tabelle `object_type_layout` (PK `(object_type_id, purpose)`) speichert **ausschließlich explizit abweichende Layouts** — kein Eintrag bedeutet "noch keine Abweichung vom generierten Standard".
- `GET /object-types/{id}/layouts/{purpose}` liefert bei fehlendem Eintrag ein **on-the-fly generiertes Smart Layout** (`object_type_service.layout.generate_smart_layout`), erkennbar an `is_custom: false`. Existiert ein Eintrag, wird dieser unverändert zurückgegeben (`is_custom: true`).
- `PUT .../layouts/{purpose}` schreibt einen expliziten Override; `DELETE .../layouts/{purpose}` entfernt ihn wieder (Reset auf den generierten Standard) — idempotent, kein Fehler, falls nie einer existierte.
- Feld-Anzeigenamen (`label`) leben **im Layout**, nicht als neues Feld an der Attribut-Definition selbst — das Smart Layout kopiert beim Generieren zunächst den technischen Attributnamen als Label.

## Begründung

- **Ein generierter Default, der sofort bei Objekttyp-Anlage persistiert würde, veraltet automatisch bei jeder späteren Attributänderung** (neues Attribut hinzugefügt, `required` geändert) — die gespeicherte Kopie müsste dann bei jeder Attribut-Änderung aktiv nachgeführt werden, inklusive der Frage, was mit einem inzwischen von Hand angepassten Layout passieren soll. Ohne Persistierung des Defaults entfällt dieses Synchronisationsproblem komplett: Ein noch nicht angepasstes Layout ist per Definition immer aktuell, weil es bei jedem Lesezugriff frisch aus der aktuellen Attributliste berechnet wird.
- **Explizite Overrides sind bewusst ein Snapshot, keine live Referenz.** Sobald eine administrierende Person das generierte Layout im künftigen Layout-Designer (P5b-S3) anpasst und speichert, wird genau dieser Stand eingefroren (inkl. der zum Speicherzeitpunkt gültigen `required`-Flags) — nachträgliche Attributänderungen am Objekttyp aktualisieren ein bereits gespeichertes, individuelles Layout nicht automatisch. Das ist dieselbe bewusste Nicht-Rückwirkung wie bei `allowedParentTypes` (ADR 0013): Konsistenzprüfung nur bei der jeweiligen Schreiboperation, kein Hintergrundabgleich über den gesamten Bestand.
- **Referenzprüfung beim Schreiben, nicht erst beim Lesen**: `PUT` lehnt Layouts ab, die ein nicht (mehr) existierendes Attribut referenzieren (`422`), analog zur `allowedParentTypes`-Referenzprüfung (ADR 0013) — verhindert stille, nie sichtbare Karteileichen im Layout.
- **Dieselbe Generierungs-Heuristik für alle drei Verwendungszwecke** (2 Spalten pro Zeile, Attributreihenfolge) statt drei verschiedener Default-Algorithmen — Zweck-spezifische Unterschiede entstehen laut Konzept ausschließlich durch individuelles Nachjustieren im Layout-Designer, nicht durch unterschiedliche Ausgangs-Layouts.
- **`label` gehört zum Layout, nicht zur Attribut-Definition**: Das Konzept beschreibt die Anzeigename-Vergabe als Teil der Layout-Generierung ("vergibt je Attribut einen sprechenden Anzeigenamen … aus diesen Angaben leitet das System automatisch ein Standardlayout ab"), nicht als eigenständiges Attribut-Schema-Feld. Da ein Attribut potenziell unterschiedliche Labels in Anzeige/Suche/Upload haben könnte (unwahrscheinlich, aber vom Datenmodell nicht ausgeschlossen), passt das besser zum jeweiligen Layout als zur einmal je Objekttyp definierten Attributliste.
- **Eigene Tabelle statt JSON-Spalte an `object_type`**: Drei unabhängig überschreib-/zurücksetzbare Layouts (je ein `PUT`/`DELETE` pro Zweck) sind mit einer eigenen Zeile pro `(object_type_id, purpose)` einfacher zu handhaben als drei verschachtelte Schlüssel in einer gemeinsamen JSON-Spalte, insbesondere für das granulare `DELETE` (Reset nur eines einzelnen Zwecks).

## Konsequenzen

- Neue Tabelle `object_type_layout`, per Fremdschlüssel (`ON DELETE CASCADE`) an `object_type.object_type` gebunden — beim Löschen eines Objekttyps verschwinden dessen Layout-Overrides automatisch mit, keine eigene Aufräumlogik nötig.
- Kein Rückwirkungs-Check, wenn sich die Attributliste eines Objekttyps ändert, nachdem bereits ein individuelles Layout gespeichert wurde — ein gespeichertes Layout kann danach ein inzwischen entferntes Attribut referenzieren (dieselbe Klasse von Inkonsistenz wie bei `allowedParentTypes`, siehe ADR 0013). Wird als offener Punkt dokumentiert, nicht in dieser Session behoben.
- Admin-UI-Bedienung (Layout-Designer zum Nachjustieren, Anzeigename-Vergabe) folgt erst mit **P5b-S3** — diese Session deckt ausschließlich Backend-Datenmodell, Smart-Layout-Generierung und die Lese-/Schreib-/Reset-API ab, verifiziert über pytest/curl.
- User-UI-Konsum der Layouts (Metadaten-Panel, Suchmaske, Upload-Dialog von fest verdrahteten Formularen auf layoutgesteuertes Rendering umstellen) folgt erst mit **P5b-S4**.
