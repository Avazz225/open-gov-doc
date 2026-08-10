# 0012 — Suche: Postgres Volltextsuche statt dediziertem Suchindex

**Status:** akzeptiert
**Kontext:** Konzept 3.7, Session P5-S4

## Entscheidung

Der neue `search-service` implementiert "Volltextindex + Facettensuche" (3.7) über **Postgres' eingebaute Volltextsuche** statt eines dedizierten Suchindex (Elasticsearch/Meilisearch/OpenSearch): eine `tsvector`-Spalte (`search_vector`) je indexiertem Dokument, aufgebaut aus `setweight(to_tsvector('german', title), 'A') || setweight(to_tsvector('german', full_text), 'B')` (Titeltreffer werden höher gewichtet als Volltext-Treffer), GIN-indexiert, abgefragt über `websearch_to_tsquery('german', ...)` und `ts_rank(...)` für die Relevanzsortierung.

## Begründung

- **Dieselbe Abwägungsklasse wie ADR 0010 (EicarSignatureEngine statt ClamdEngine) und ADR 0011 (Tesseract statt PaddleOCR)**: ein dedizierter Suchindex bedeutet einen weiteren Container, weitere Betriebsfläche, zusätzlichen Speicher-/Startzeit-Aufwand für ein reproduzierbares `docker compose up --build` — vermieden, wenn eine bereits vorhandene, native Alternative ausreicht.
- **Postgres bringt eine eingebaute `german`-Textsuchekonfiguration mit** (Stemming, Stoppwörter) — keine zusätzliche Extension nötig. `pg_trgm`/`unaccent` sind in der laufenden Postgres-Instanz zwar verfügbar (`pg_available_extensions`), werden aber diese Session bewusst **nicht** installiert/verdrahtet: das Konzept verlangt nur "Volltextindex + Facettensuche", keine Tippfehlertoleranz — beides bleibt als naheliegende, günstige spätere Erweiterung dokumentiert (`CREATE EXTENSION pg_trgm`, `similarity()`-Fallback bei schwachen `ts_rank`-Treffern), aber nicht Teil dieser Session.
- **Postgres ist bereits die zentrale Datenbank jedes Service in diesem System** (3.1: ein Schema je Service) — kein neuer Infrastruktur-Baustein, keine zusätzliche Synchronisationslogik zwischen zwei Speichersystemen.
- **Facettensuche (Objekttyp/Ordner/Attribute/Ersteller/Datum) ist mit normalen SQL-`WHERE`-Klauseln und `GROUP BY`-Aggregation vollständig abbildbar** — kein Bedarf an einer spezialisierten Facetten-Engine für den hier verlangten Umfang.

## Konsequenzen

- Relevanzsortierung ist solide, aber nicht auf dem Niveau von BM25/spezialisierten Ranking-Algorithmen dedizierter Suchmaschinen — für den Umfang dieser Session (Volltextindex + Facetten) ausreichend.
- ~~Keine Tippfehlertoleranz/Fuzzy-Matching in dieser Session (`pg_trgm` wäre der naheliegende Erweiterungspfad, siehe oben).~~ Geschlossen in P14-S7: `pg_trgm` ist seither installiert, `query_language.py`/`query_compiler.py` bauen zusätzlich Fuzzy- und Näherungssuche sowie Wildcards obendrauf - siehe [ADR 0044](0044-search-query-language-fuzzy-proximity.md).
- Attributfilter auf dem JSONB-`attributes`-Feld sind auf einfache Exakt-/Bereichsvergleiche beschränkt (`->>`-Textextraktion + Typ-Cast) — keine komplexen verschachtelten Attributstrukturen, was aber dem aktuellen, flachen Objekttyp-Attributschema entspricht (2.2).
- Ein Wechsel zu einem dedizierten Suchindex bleibt möglich, falls Skalierungs- oder Relevanzanforderungen das später erfordern — die Indexierungs-Pipeline (`consumer.py`/`pipeline.py`) ist bereits vom Speichermechanismus (`repository.py`) getrennt.
