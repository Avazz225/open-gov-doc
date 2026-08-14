# 0086 — HTML-Vorschau: serverseitige Blockade externer Subressourcen

**Status:** akzeptiert (Session 3 von 4, siehe Phase 21 in `IMPLEMENTATION_PLAN.md`)
**Kontext:** Post-Roadmap Phase 21 Session 3, betrifft `document-service`, dokumentiert für `user-ui`

## Entscheidung

`user-ui`s `PreviewPane` rendert HTML-Dokumente über ein `sandbox=""`-iframe mit `srcDoc` (kein `src` auf
eine eigene Origin). `sandbox=""` blockiert Skriptausführung/Formulare/Top-Navigation, aber NICHT das
normale Nachladen von Subressourcen (Bilder, Stylesheets, ...) — ein hochgeladenes HTML-Dokument mit z. B.
`<img src="https://tracker.example/pixel.gif?...">` löst diese Anfrage beim bloßen Öffnen der Vorschau
aus, unabhängig vom Sandbox-Attribut (Tracking-/Datenleck-Risiko). Ein `Content-Security-Policy`-Header
wäre die naheliegende Lösung, wirkt bei `srcDoc`-Inhalten ohne eigene Origin aber nur eingeschränkt
(kein fester Origin, an den sich ein Header zuverlässig binden ließe — abhängig von Browser/Engine).

`document-service` neutralisiert externe `src`/`href`-Referenzen deshalb **serverseitig, an der Quelle**,
statt am Rendering-Ort: `GET /documents/{id}/content` und `GET /documents/{id}/versions/{n}/content`
prüfen den (bereits per Magic-Byte-Sniffing bestimmten, nicht dem Client vertrauten) `content_type` —
bei `"text/html"` läuft der Inhalt vor der Auslieferung durch eine neue Funktion
`html_preview_guard.rewrite_external_references`.

1. **Parsing mit `BeautifulSoup`/`html.parser`** (neue Abhängigkeit) — entfernt jede `src`/`href`-
   Referenz, die nicht `data:`/`mailto:`/`tel:` oder ein reiner Fragment-Anker (`#...`) ist.
   Attribut-getrieben statt Tag-Namen-getrieben (kein hartkodiertes `img`/`script`/`iframe`/...-Set) —
   erfasst automatisch auch unübliche Tags mit `src`/`href`.
2. **Relative Pfade werden ebenfalls blockiert**, nicht nur absolute URLs — ein `srcDoc`-Inhalt hat keine
   sichere, im Vorschaukontext auflösbare Basis-URL (abhängig von Browser-Implementierung könnten
   relative/schema-relative Pfade unvorhersehbar auf die übergeordnete Seite oder unerwartete Ziele
   auflösen).
3. **Sichtbare Markierung statt stillschweigender Entfernung** — direkt nach jeder entfernten Referenz
   wird `[Blockierte externe Anfrage: <ursprüngliche-URL>]` als sichtbares `<span>` eingefügt, wörtliche
   Plan-Vorgabe.
4. **`<meta charset>` wird auf `utf-8` normalisiert** — die Funktion liefert immer UTF-8-Bytes zurück,
   unabhängig vom ursprünglich deklarierten Encoding des hochgeladenen Dokuments.

## Begründung

- **Warum serverseitiges Rewriting statt eines CSP-Headers** (explizite Plan-Vorgabe, hier bestätigt):
  ein `srcDoc`-iframe hat keinen eigenen, adressierbaren Origin/keine eigene URL, an die sich ein
  `Content-Security-Policy`-Header zuverlässig binden ließe — Verhalten ist browserabhängig und nicht
  robust genug für diesen Zweck. Das Entfernen der Referenz an der Quelle (bevor der Browser sie
  überhaupt sieht) ist unabhängig von Browser-CSP-Eigenheiten wirksam.
- **Warum `BeautifulSoup` als neue Abhängigkeit statt eines regex-basierten Ansatzes oder purer
  `html.parser`-Handler**: attributweises Rewriting mit korrekter Serialisierung (Quoting, Selbstschluss-
  Tags, Entity-Behandlung) ist mit rohem Regex auf verschachteltem, potenziell fehlerhaftem HTML
  fehleranfällig; `html.parser.HTMLParser` (Stdlib) ist ein reiner SAX-artiger Event-Handler ohne
  eingebaute Baumrepräsentation/Serialisierung, ein korrektes Wieder-Zusammensetzen müsste von Hand
  nachgebaut werden. `BeautifulSoup` mit dem `html.parser`-Backend braucht keine zusätzliche
  C-Erweiterung (anders als `lxml`), ist ein extrem etablierter Pfad für genau diese Aufgabe
  (untrusted HTML parsen, gezielt Attribute mutieren, zurückserialisieren) und rechtfertigt die neue
  Abhängigkeit angesichts der sicherheitsrelevanten Korrektheitsanforderung.
- **Warum attribut- statt tag-getrieben**: ein festes Tag-Set (`img`/`script`/`iframe`/...) müsste bei
  jedem neuen/unüblichen Tag mit `src`/`href` (z. B. `<object data=...>` wäre ohnehin ein Sonderfall,
  aber `<portal>`, benutzerdefinierte Elemente mit `src`-artigen Attributen) einzeln nachgepflegt werden
  - die attributgetriebene Prüfung ist dagegen vollständig unabhängig vom konkreten Tag-Namen.
- **Warum relative Pfade ebenfalls blockiert werden** (nicht nur absolute externe URLs): ein
  hochgeladenes HTML-Dokument in diesem System hat kein legitimes Zielverzeichnis mit Geschwisterdateien,
  die über einen relativen Pfad erreichbar wären (das Dokument ist ein einzelnes gespeichertes Objekt,
  keine Verzeichnisstruktur) — ein relativer Pfad kann also ohnehin nie auf einen echten, beabsichtigten
  Inhalt zeigen, nur auf etwas Unvorhergesehenes.
- **Warum bewusst nur `src`/`href`, nicht auch `srcset`/`poster`/`background`/CSS-`url(...)`**: explizite
  Plan-Vorgabe ("externe `src`/`href`") — eine vollständige Abdeckung aller denkbaren
  Subressourcen-Vektoren wäre eine deutlich größere Aufgabe (CSS-Parsing für `url()` in `<style>`-Blöcken
  und `style`-Attributen); als bewusst unvollständige, aber dokumentierte erste Härtungsstufe umgesetzt,
  konsistent mit diesem Projekts Muster, Grenzen ehrlich zu dokumentieren statt stillschweigend
  Vollständigkeit vorzutäuschen.

## Konsequenzen

- **Neue Abhängigkeit** `beautifulsoup4` in `services/document-service/pyproject.toml` — erstes
  HTML-Parsing-Tool in diesem Repo (bisher nur `lxml` für XML/BPMN in anderen Services, kein Präzedenzfall
  für HTML).
- **Betrifft nur `document-service`s `GET /documents/{id}/content`/`.../versions/{n}/content`** — die
  separate `GET /public/share-links/content` (öffentlicher Freigabelink-Download) rendert nicht inline
  (reiner `<a href download>`-Browser-Download in `user-ui`s `SharePage`, kein `srcDoc`-iframe), daher
  bewusst außerhalb des Sessionsumfangs.
- **Nicht-HTML-Inhalte bleiben byte-identisch** — die Umschreibung greift ausschließlich bei
  `content_type == "text/html"` (Magic-Byte-Sniffing-Ergebnis, nicht der ungeprüfte Client-Header).
- **Tests**: `document-service` 247 (vorher 234, +13) — neue Testdatei `test_html_preview_guard.py` (11
  reine Funktionstests: externe HTTPS-/protokollrelative-/relative-Referenzen blockiert, `data:`/
  `mailto:`/`tel:`/Fragment-Anker erlaubt, `javascript:`-URIs blockiert, leerer `src` blockiert,
  `<meta charset>`-Normalisierung, unveränderter Inhalt ohne Referenzen), plus 2 neue `test_api.py`-Tests
  (Ende-zu-Ende über beide Download-Endpunkte inkl. Markierungstext, Byte-Identität für Nicht-HTML-Inhalte
  mit zufällig HTML-ähnlichem Text als Regressionsschutz).
- Doku: `docs/services/document-service.md` (neue "HTML-Vorschau-Härtung"-Sektion, API-Tabelle),
  `docs/services/user-ui.md` ("Offene Punkte" als behoben markiert).
