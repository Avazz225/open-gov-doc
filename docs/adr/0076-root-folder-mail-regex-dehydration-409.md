# 0076 — Root-Ordner-Schutz, formatabgeleitete Mail-Erkennung, Dehydrierungs-409

**Status:** akzeptiert (Session 11 von 11, letzte Session in Phase 19, siehe `IMPLEMENTATION_PLAN.md`)
**Kontext:** Post-Roadmap Phase 19 Session 11, betrifft `folder-service`, `mail-connector`,
`document-service`, `apps/user-ui`

## Entscheidung

Drei unabhängige, kleine Lücken aus der "Offene Punkte"-Triage, gebündelt in einer Session:

1. **`folder-service`: `root` als geschützter Sonderordner.** `PROTECTED_FOLDER_IDS`
   (`settings.py`) enthielt bislang nur `inbox`/`outbox`, nicht `root` — `root` konnte umbenannt,
   verschoben, hart gelöscht oder in den Papierkorb verschoben werden, obwohl er als einziger Ordner
   `parent_id=null` trägt und von jedem anderen Service als feste, immer existierende Wurzel
   vorausgesetzt wird. `root` wird jetzt in dieselbe `frozenset` aufgenommen wie `inbox`/`outbox` und
   durchläuft damit automatisch dieselben drei bestehenden `409`-Prüfungen in `main.py`
   (`update_folder`, `hard_delete_folder`, `trash_folder`) — keine neue Prüf-Logik, nur eine erweiterte
   Mitgliedschaft in der bereits bestehenden Menge.
2. **`mail-connector`: Kandidaten-Erkennung aus den tatsächlich konfigurierten Formaten statt
   hartkodiert.** `matching.py`s Kandidaten-Regex war ein generisches `[A-Za-z0-9]{2,10}[-/][A-Za-z0-9]{2,10}`
   — deckte zufällig die beiden Default-Formate ab, aber keine installationsspezifisch abweichenden
   `kennzeichen_format`/`CaseNumberConfig.format`-Werte. Neue `build_candidate_pattern(formats)` leitet
   das Muster direkt aus den `{Platzhalter}`-Formatstrings von `object-type-service` und `case-service`
   ab (erste Rückumwandlung Format→Regex im Projekt, bisher lief `str.format()` nur vorwärts). Ergebnis
   wird pro eingehender Nachricht frisch geladen (nicht einmalig gecacht), mit Rückfall auf das alte
   generische Muster, falls einer der beiden Cross-Service-Aufrufe fehlschlägt.
3. **`document-service`: `409` statt `404` beim Download eines ausgesonderten (dehydrierten)
   Dokumentinhalts.** `GET /documents/{id}/content` und `GET /documents/{id}/versions/{n}/content`
   prüfen jetzt `document.dehydrated_at is not None` VOR dem Storage-Aufruf und liefern `409` mit einem
   Hinweis auf die nötige Rückholung — vorher lieferte der fehlgeschlagene Storage-Download ein
   generisches `404`, das identisch aussah wie eine echte Dateninkonsistenz (siehe
   `test_download_content_returns_404_instead_of_crashing_if_object_missing`). `apps/user-ui`s
   `PreviewPane.tsx` zeigt diese `409`-Meldung jetzt sichtbar an — die bisherige Download-Fehlerbehandlung
   war komplett stillschweigend (weder bei `409` noch bei irgendeinem anderen Fehler erschien etwas).

## Begründung

- **Warum `root` erst jetzt und nicht von Anfang an geschützt**: die ursprüngliche Sonderordner-Logik
  (P15-S1/S3) entstand für `inbox`/`outbox` als neue, zusätzliche Sonderordner — `root` existierte davor
  bereits und wurde in der Recherche zu dieser Session als übersehene Lücke identifiziert (kein Nutzer
  hatte je versucht, `root` umzubenennen; das Verhalten war ein Bug durch Auslassung, kein bewusst
  offen gelassenes Verhalten).
- **Warum keine neue Prüf-Logik nötig war**: alle drei Schutzstellen (`main.py:539-545`, `:605-608`,
  `:635-639` laut Recherche) prüfen bereits gegen `folder_id in PROTECTED_FOLDER_IDS` als Menge, nicht
  gegen eine feste Liste einzelner IDs — eine reine Konfigurationsänderung genügt.
- **Warum die Kandidaten-Regex aus echten Formaten abgeleitet wird statt eines zweiten Hardcodes**: eine
  Installation kann `kennzeichen_format`/`CaseNumberConfig.format` beliebig konfigurieren (2.2/2.5) — ein
  hartkodiertes Muster, das nur zufällig zu den Defaults passt, würde bei jeder Abweichung (z. B. drei-
  statt vierstelliges Jahr, ein zusätzliches Trennzeichen) unbemerkt keine Kandidaten mehr finden, ohne
  dass dies irgendwo sichtbar würde.
- **Warum pro Nachricht neu geladen statt einmalig gecacht**: ein einmaliger Fetch beim App-Start würde
  neu angelegte Objekttypen/Formate erst nach einem Neustart erkennen — der Preis (zwei zusätzliche
  Cross-Service-Aufrufe je eingehender Mail) ist angesichts des Nachrichtenvolumens vernachlässigbar.
  Wichtiger technischer Grund, der während der Umsetzung entdeckt wurde: ein wiederverwendeter,
  langlebiger HTTP-Client aus `app.state` (an die Event-Loop des Lifespan-Kontexts gebunden) führt in
  Tests, die `_ingest_message()` direkt statt über `TestClient`s Request-Dispatch aufrufen, zu
  `RuntimeError: ... bound to a different event loop` — frische, kurzlebige Client-Instanzen je Aufruf
  umgehen dieses Problem strukturell.
- **Warum `409` statt `404` bei Dehydrierung**: `404` ("nicht gefunden") und "bewusst aus dem
  Primärspeicher entfernt, aber wiederherstellbar" sind semantisch verschieden — ein Nutzer, der `404`
  sieht, hat keinen Hinweis darauf, dass eine Rückholung überhaupt möglich ist. `409` (Konflikt: der
  Zustand des Dokuments erlaubt die angeforderte Operation aktuell nicht) mit erklärendem `detail` folgt
  demselben Muster wie die bereits bestehenden `409`-Antworten dieses Service (z. B. Sonderordner-Schutz
  in `folder-service`, siehe oben).
- **Warum keine `archival_transfer_id`-Verlinkung in der Fehlermeldung**: `document.dehydrated_at` trägt
  keine Referenz auf den zugehörigen `ArchivalTransfer`/`CaseArchivalTransfer` — eine Verlinkung hätte
  ein neues Datenfeld erfordert, das über den Rahmen dieser kleinen, isolierten Session hinausgegangen
  wäre. Die Meldung bleibt bewusst generisch ("muss erst zurückgeholt werden").

## Konsequenzen

- **Tests**: `folder-service` 120 (vorher 116, +4: Umbenennen/Verschieben/Hart-Löschen/Papierkorb für
  `root`, spiegelbildlich zu den bestehenden Inbox-Tests). `mail-connector` 33 (vorher 30, +3: gezielte
  Unit-Tests für `build_candidate_pattern`, siehe unten den Regressionsfund; ein Testdatum musste
  zusätzlich von einem hex- auf ein rein numerisches Suffix umgestellt werden, da die neue, strengere
  Regex `Laufende_Nummer` als `\d+` statt generisch alphanumerisch erkennt — der reale,
  systemgenerierte Kennzeichen-Suffix ist ohnehin immer numerisch, siehe `_render_kennzeichen`).
  `document-service` 234 (vorher 233, +1: neuer `409`-Roundtrip-Test inkl. Rehydrierung).
  `apps/user-ui`: 171 Tests (vorher 169, +2: `409`-spezifische und generische Download-Fehlermeldung),
  `tsc`/`eslint`/`next build` clean.
- **Zwei echte Bugs bei der Live-Verifikation gefunden und behoben** (beide erst sichtbar geworden, weil
  diese Session zum ersten Mal einen echten SMTP→POP3-Roundtrip mit einem installationsspezifisch
  konfigurierten, vom Default abweichenden Format durchspielte, statt nur die ohnehin passenden
  Default-Formate zu testen):
  1. **`infra/docker-compose.yml`s `mail-connector`-Block hatte kein `DMS_OBJECT_TYPE_SERVICE_BASE_URL`**
     — fiel im Container auf `Settings`s Lokal-Dev-Default (`http://localhost:8007`) zurück, der dort ins
     Leere zeigt. `list_kennzeichen_formats()` schlug dadurch bei jedem Nachrichteneingang fehl (mit
     Log-Warnung), das Kandidaten-Muster nutzte still den generischen Rückfall — funktional unauffällig
     für die beiden Default-Formate (die deckt der Rückfall zufällig ab), aber genau die neue Fähigkeit
     dieser Session (installationsspezifische Formate erkennen) blieb dadurch komplett wirkungslos.
     Behoben durch Ergänzen der Variable (gleiches Muster wie bei jedem anderen Service dieses Projekts)
     plus `depends_on: object-type-service`.
  2. **`build_candidate_pattern` sortierte die Formate alphabetisch statt nach Länge** — Pythons
     `re`-Alternation ist "erste passende Alternative gewinnt", kein längster-Treffer-Matching wie POSIX.
     Mit drei live tatsächlich konfigurierten, unterschiedlichen Formaten (`{Federführung}-
     {Laufende_Nummer}`, `{Federführung}-{YYYY}-{Laufende_Nummer}`, `{YYYY}-{Laufende_Nummer}`) sortierte
     `sorted(set(formats))` das KÜRZERE, Jahr-lose `{Federführung}-{Laufende_Nummer}`-Muster alphabetisch
     vor das längere — ein echter Kandidat wie `P19S11Y-2026-004` wurde dadurch fälschlich bereits nach
     `P19S11Y-2026` abgeschnitten (die erste Alternative `\S+?-\d+` fand dort schon ihren vollständigen,
     kürzeren Treffer und wurde nie durch die zweite, korrektere Alternative ersetzt). Behoben durch
     Sortierung nach absteigender Formatlänge (`key=lambda f: (-len(f), f)`) statt alphabetisch — die
     längste, spezifischste Alternative wird jetzt immer zuerst versucht. Drei neue gezielte Unit-Tests
     in `test_matching.py` (inkl. eines direkten Regressionstests mit genau dieser Format-Kombination).
- **Eine vorbestehende, unabhängige Testinfrastruktur-Flakiness** in `mail-connector`s
  `test_confirm_match_creates_document_in_matched_folder` (sporadisches "different event loop" bei
  einem langlebigen `app.state.virus_scan`-Client in Kombination mit direktem `_ingest_message()`-Aufruf)
  wurde während der Fehlersuche identifiziert, aber NICHT behoben — außerhalb des Sessionumfangs, per
  mehrfachem Isolationslauf als bereits vor dieser Session bestehend bestätigt.
- **`rendering-service`/`ocr-service`/`signature-service`s Document-Clients** rufen weiterhin
  `response.raise_for_status()` ohne spezielle `409`-Behandlung auf `GET
  .../versions/{n}/content` — ein `409` propagiert dort als generischer `httpx.HTTPStatusError`,
  abgefangen vom jeweils bereits bestehenden breiten `except Exception` der Poll-Loops (gleiches
  Fehlerbild wie das vorherige `404`, kein Regressionsrisiko). Keine Änderung an diesen drei Clients in
  dieser Session — außerhalb des in der Roadmap benannten Umfangs.
- **Live gegen den echten laufenden Stack verifiziert** (nach Image-Neubau von `folder-service`,
  `mail-connector`, `document-service` — `mail-connector` musste dabei zweimal neu gebaut werden, siehe
  die beiden oben beschriebenen Bugfixes): `root`-Umbenennung/-Verschiebung/-Hart-Löschung/-Papierkorb
  liefert jeweils `409`; ein echter SMTP→POP3-Roundtrip gegen `mailpit` mit einem live über
  `document-service` erzeugten, installationsspezifisch attributbasierten Kennzeichen
  (`P19S11Z-2026-005`, Objekttyp "Akte", Format `{Federführung}-{YYYY}-{Laufende_Nummer}`) wird nach
  beiden Bugfixes korrekt und vollständig als `status="proposed_match"` mit passendem `document_id`
  erkannt (zwei Zwischenversuche mit kollidierenden bzw. abgeschnittenen Kandidaten dokumentierten dabei
  die beiden Bugs, siehe oben); ein dehydriertes Dokument liefert `409` mit Rückhol-Hinweis statt `404`,
  nach Rehydrierung wieder `200`.
