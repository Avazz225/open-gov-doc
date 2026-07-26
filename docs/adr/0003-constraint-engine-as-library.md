# 0003 — Constraint Engine als geteilte Bibliothek statt eigenständiger Service

**Status:** akzeptiert
**Kontext:** Konzept 2.2/4.5, Session P3-S3 (Object-Type Service, Folder Service, Constraint Engine)

## Entscheidung

Die Roadmap nennt "Constraint Engine" als eigenständiges Konzept neben dem
Object-Type Service. Umgesetzt wurde sie **nicht** als eigener Microservice,
sondern als reine, zustandslose Python-Bibliothek (`libs/dms-constraint-engine`,
`validate(schema, name, attributes) -> list[str]`), eingebettet ausschließlich
im **Object-Type Service**, der sie über seinen `POST /object-types/{id}/validate`-
Endpunkt nach außen anbietet. Folder Service und Document Service rufen diesen
HTTP-Endpunkt auf, importieren die Lib aber nicht selbst.

## Begründung

Die eigentliche Validierungslogik ist eine reine Funktion ohne eigenen
Zustand, keine eigene Persistenz, keine eigenen Events, keine Notwendigkeit,
unabhängig vom Object-Type Service skaliert oder deployt zu werden - sie
braucht die Objekttyp-Definition (die der Object-Type Service ohnehin schon
persistiert) als einzigen externen Input. Ein eigener Microservice nur für
eine zustandslose Funktion hätte lediglich zusätzliche Netzwerk-Hops,
Health-Checks, ein eigenes (leeres) Postgres-Schema und Compose-Einträge
erzeugt, ohne einen architektonischen Vorteil zu bieten (kein unabhängiger
Skalierungsbedarf, keine unabhängige Verfügbarkeitsanforderung).

Die Trennung "Lib" (Logik) vs. "Object-Type Service" (Persistenz + API) folgt
demselben Muster wie `dms-eventbus-client`/`dms-db-base`: gemeinsamer Code lebt
in `libs/`, wird aber in genau einem Servicekontext eingebettet, nicht über
Service-Grenzen hinweg direkt importiert. Document Service und Folder Service
sprechen ausschließlich die HTTP-API des Object-Type Service an - kein Import
fremder Service-Interna, konsistent mit der übrigen Architektur.

## Konsequenzen

- Ein zusätzlicher Netzwerk-Hop pro Validierung (Document/Folder Service →
  Object-Type Service) statt eines In-Process-Aufrufs - für den aktuellen
  Anwendungsfall (Validierung bei Erstellung, kein Hot Path mit hoher
  Frequenz) unkritisch.
- Sollte die Constraint Engine später auch von der Workflow Engine (7.1) für
  BPMN-Gateway-Bedingungen wiederverwendet werden, kann sie entweder erneut
  als Lib eingebunden werden (z. B. in einem eigenen Auswertungsdienst) oder
  über denselben `/validate`-artigen HTTP-Vertrag angesprochen werden - beide
  Wege bleiben offen, ohne dass diese Entscheidung revidiert werden muss.
- "Verweise auf andere Objekte" (`type: "reference"`) werden von der Lib nur
  auf Format geprüft (nicht-leerer String), nicht auf tatsächliche Existenz
  beim referenzierten Service - eine generische "Referenztyp → zuständiger
  Service"-Auflösung existiert nicht und wäre eine deutlich größere
  Erweiterung als der aktuelle Bedarf rechtfertigt (siehe
  `docs/services/object-type-service.md`).
