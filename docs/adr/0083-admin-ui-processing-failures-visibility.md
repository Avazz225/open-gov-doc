# 0083 — Admin-UI: "Permanent fehlgeschlagen"-Sichtbarkeit + manueller Neustart

**Status:** akzeptiert (Session 7 von 7, siehe Phase 20 in `IMPLEMENTATION_PLAN.md`)
**Kontext:** Post-Roadmap Phase 20 Session 7, betrifft `admin-ui`, `rendering-service`, `ocr-service`

## Entscheidung

Die fünf Resilienz-Sessions dieser Phase (ADR 0078–0082) machten `failed_permanent`/`delivery_failed`
serverseitig sichtbar und retry-fähig, aber ohne jede Admin-UI-Anbindung. Diese Session schließt die
Lücke für die vier vom Plan explizit genannten Services (`archival-service` bereits mit vorhandener
Admin-UI-Seite, `notification-service`/`rendering-service`/`ocr-service` bislang ganz ohne
UI-Sichtbarkeit) — bewusst **ohne** `federation-hub-service`, das der Plan an dieser Stelle nicht nennt.

1. **`ArchivalTransfersView`** (bereits vorhanden) bekommt `failed_permanent` als neue
   Filter-Option in beiden Sektionen (Dokument- und Umlaufmappen-Aussonderung) sowie einen "Erneut
   versuchen"-Button, der nur bei diesem Status erscheint (`POST .../retry`, bereits seit ADR 0078
   serverseitig vorhanden).
2. **Neue, gemeinsame Seite `/processing-failures/`** (`ProcessingFailuresView`, drei eigenständige
   Sektionen: Benachrichtigungen, Ersatzdarstellungen, OCR-Ergebnisse) statt dreier eigener Seiten — jede
   Sektion lädt ausschließlich `status=failed_permanent`-Datensätze des jeweiligen Service und bietet
   einen Neustart-Button je Zeile.
3. **`rendering-service`/`ocr-service`: `GET /renditions`/`GET /ocr-results` bekommen `document_id`
   optional** (vorher Pflichtparameter) plus einen neuen `status`-Query-Parameter — ohne das wäre eine
   dokumentübergreifende "alle fehlgeschlagenen Renditions/OCR-Ergebnisse"-Ansicht technisch nicht
   möglich gewesen. `notification-service`s `GET /notifications` hatte bereits einen optionalen
   `status`-Filter und brauchte keine Änderung.

## Begründung

- **Warum EINE gemeinsame neue Seite statt dreier**: keiner der drei Services hatte bereits eine
  passende bestehende Admin-UI-Seite, in die eine kleine Sektion natürlich gepasst hätte (anders als bei
  `archival-service`) — der Plan erlaubt ausdrücklich "neue kleine Sektion statt eigener Seite". Drei
  komplett neue Einzelseiten für dieselbe Art von Inhalt (Liste + Neustart-Button) wären unnötige
  Navigationszersplitterung; eine gemeinsame Seite mit drei Sektionen folgt demselben, bereits etablierten
  Mehr-Sektionen-Muster wie `ArchivalTransfersView` (Dokument-/Case-Sektion in einer Seite).
- **Warum `document_id` bei `rendering-service`/`ocr-service` optional statt eines separaten
  Admin-Endpunkts**: beide Endpunkte sind bereits über `rendering.read`/`ocr.read`
  (`permission-service`, seit ADR 0073) gegated — ein Aufruf ohne `document_id` unterliegt derselben
  Berechtigungsprüfung wie mit, es entsteht keine neue, ungegatete Angriffsfläche. Ein separater
  `/admin/...`-Endpunkt hätte dieselbe Berechtigung dupliziert, ohne einen echten Sicherheitsgewinn.
- **Warum kein neuer, dedizierter "Badge"-Farbton für `failed_permanent`**: `admin-ui`s CSS kennt aktuell
  nur zwei Badge-Varianten (`ok`/`down`, siehe `globals.css`), konsistent über drei Theme-Varianten
  (hell/dunkel/Kontrast) gepflegt. Eine dritte Variante allein für diese Session hinzuzufügen wäre
  Scope-Creep über eine reine Sichtbarkeits-/Neustart-Session hinaus — die Unterscheidung zu `failed`
  passiert stattdessen über den (bereits übersetzten, klar unterscheidbaren) Status-Text selbst
  ("Fehlgeschlagen" vs. "Dauerhaft fehlgeschlagen") und den nur bei `failed_permanent` sichtbaren
  Neustart-Button.
- **Warum `federation-hub-service` NICHT Teil dieser Session ist**: der Plan nennt in der P20-S7-Zeile
  explizit nur Archiv-/Notification-/Rendition-/OCR-Fehler, nicht Handover — konsistent mit ADR 0081s
  eigenem Scope (nur die Erstzustellung, nicht die Ergebnis-Rückleitung). Eine Admin-UI-Sichtbarkeit für
  fehlgeschlagene Handover wäre eine sinnvolle eigenständige Folgesession, kein Bestandteil dieser.

## Konsequenzen

- **`ArchivalTransfer`/`CaseArchivalTransfer`-Frontend-Typen** bekommen die zuvor fehlenden Felder
  `attempts`/`next_retry_at` (server-seitig seit ADR 0078 vorhanden, im Frontend bislang nicht
  abgebildet).
- **`statusLabel`/`caseStatusLabel`-Hilfsfunktionen** wurden auf PascalCase-je-Wortteil umgestellt
  (`"failed_permanent"` → `"archivalTransfers.statusFailedPermanent"` statt des vorherigen, nur den
  ersten Buchstaben großschreibenden Musters, das einen unauffindbaren i18n-Key erzeugt hätte).
- **Bestehende `rendering-service`/`ocr-service`-Aufrufer bleiben unverändert kompatibel**: alle
  bisherigen Aufrufe übergeben `document_id` bereits explizit als Keyword-Argument bzw. Query-Parameter —
  das Optional-Machen ist rein additiv, kein Breaking Change.
- **Tests**: `rendering-service` 46 (vorher 44, +2), `ocr-service` 53 (vorher 51, +2, plus 8 weiterhin
  `tesseract`-gegatete Skips) — je ein Repository- und ein API-Test für den `document_id`-optional-Pfad.
  `admin-ui` 173 (vorher 166, +7): 3 neue Tests in `archival-transfers.test.tsx` (kein Retry-Button bei
  nur `failed`, Retry bei `failed_permanent` für Dokument- UND Case-Sektion), neue
  `processing-failures.test.tsx` mit 6 Tests (Laden/Anzeigen aller drei Sektionen mit dem
  `status`-Filter, Leerzustände, Unreachable-Zustand, je ein Retry-Testfall pro Sektion).
- **Live gegen den echten laufenden Stack verifiziert** (Image-Neubau + Neustart von `rendering-service`,
  `ocr-service`, `admin-ui`): `GET /renditions?status=failed_permanent` und `GET
  /ocr-results?status=failed_permanent` liefern ohne `document_id` echte, aus früheren
  Live-Verifikationen dieser Phase stammende `failed_permanent`-Datensätze über mehrere Dokumente hinweg
  (bestätigt die dokumentübergreifende Filterung mit echten Daten, nicht nur synthetischen
  Testfixtures); `GET /notifications?status=failed_permanent` liefert korrekt eine leere Liste (kein
  entsprechender Datensatz vorhanden); die neue `/processing-failures/`-Route wird vom
  `admin-ui`-Container ausgeliefert (`200`). Kein interaktiver Browser-Klickdurchlauf durchgeführt
  (dieses Projekt verifiziert Frontend-Arbeit durchgängig über `tsc`/`eslint`/`vitest`/`next build` plus
  echte Backend-Live-Checks, siehe `CONTRIBUTING.md` "Definition of Done" — kein Playwright/Browser-
  Automatisierungswerkzeug ist irgendwo im Monorepo vorhanden).
