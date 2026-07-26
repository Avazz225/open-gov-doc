# 0002 — Konfliktschutz bei Force-Unlock über optimistische Versionsprüfung statt "überwachter" Lock-Zustand

**Status:** akzeptiert
**Kontext:** Konzept 4.2 (Dokumentensperre bei Bearbeitung, insbesondere die "Konfliktbehandlung bei Force-Unlock"), Session P3-S2 (Document Service)

## Entscheidung

Das Konzept beschreibt für Force-Unlock einen dreiwertigen Lock-Zustand:
normal gesperrt → administrativ aufgehoben, aber **"überwacht"** → der
ursprüngliche Bearbeiter wird beim nächsten Check-in-Versuch anhand dieses
überwachten Zustands als Konfliktfall erkannt.

Der Document Service implementiert stattdessen **keinen dritten Lock-Zustand**.
Force-Unlock löscht die Sperre vollständig (`repository.force_release_lock`).
Die eigentliche Schutzwirkung entsteht durch eine **von der Sperre unabhängige,
grundsätzlich immer aktive optimistische Konflikterkennung** beim Check-in:
Jeder Versions-Upload muss `expected_base_version_number` mitgeben (die Version,
auf der die Bearbeitung beruhte). Weicht dieser Wert bei Ausführung von der
tatsächlich aktuellen Hauptversion des Dokuments ab, wird der Upload nicht
überschreibend eingespielt, sondern als eigenständige, weiterhin abrufbare
**Konfliktkopie** neben der aktuellen Version abgelegt (`<name>_conflict_<user>_<zeitstempel>`),
ohne den Hauptversions-Zeiger zu bewegen (siehe `repository.checkin_version`).

## Begründung

Beide Modelle erfüllen die im Konzept geforderte Garantie identisch: **Der
ursprüngliche Bearbeiter darf nie stillschweigend Arbeit verlieren.** Der
Unterschied liegt nur darin, *wo* die Erkennung stattfindet:

- Konzept-Variante: am Lock-Objekt selbst (ein dritter Zustand "aufgehoben,
  aber überwacht" plus Sonderlogik, die genau diesen Fall beim nächsten
  Check-in des *ursprünglichen* Halters abfragt).
  Konfliktvermeidung ergibt sich unabhängig von einer Sperre.
- Gewählte Variante: an der Versionskette selbst. Ein Check-in ist genau dann
  ein Konflikt, wenn seine Ausgangsversion nicht mehr die aktuelle ist -
  unabhängig davon, *warum* das so ist (Force-Unlock, abgelaufener Timeout,
  gar keine Sperre genommen). Force-Unlock muss dafür keinen Sonderzustand
  hinterlassen, es muss nur die Sperre wirklich freigeben, damit ein anderer
  Nutzer regulär einchecken kann.

Vorteile der gewählten Variante:

1. **Ein einziger Mechanismus statt zwei**: Die Konfliktkopie-Logik schützt
   nicht nur den Force-Unlock-Fall, sondern jeden denkbaren Wettlauf (z. B.
   zwei Check-ins kurz nacheinander ohne je eine Sperre genommen zu haben,
   oder ein abgelaufener Timeout-Unlock). Das Konzept beschreibt den
   Force-Unlock-Fall nur als Beispiel für ein allgemeineres Problem -
   die Implementierung deckt das allgemeinere Problem direkt ab.
2. Keine zusätzliche Zustandsmaschine am Lock (aktiv → überwacht → weg),
   die separat korrekt gepflegt und getestet werden müsste.
3. Entspricht dem etablierten Optimistic-Concurrency-/ETag-Muster aus
   WebDAV/CMIS, mit dem das Dokument ohnehin über externe Anwendungen
   angesprochen wird (4.2 nennt Word über WebDAV/CMIS als Beispiel).

## Konsequenzen

- Der Force-Unlock-Endpunkt (`POST /documents/{id}/lock/force-release`) selbst
  löst **kein** Notification/Audit-Ereignis mit Bezug auf eine spätere
  Konfliktkopie aus - er publiziert nur `document.lock.force_released` mit dem
  ursprünglichen Halter, sobald die Aufhebung passiert. Die eigentliche
  Konfliktkopie (falls sie später entsteht) erzeugt separat ihr eigenes
  `document.version.created`-Event mit `is_conflict: true`. Beide Ereignisse
  zusammen ergeben im Audit-Trail (Audit Service konsumiert `document.>`,
  siehe P3-S2-Änderung an dessen `subjects`) dieselbe Nachvollziehbarkeit wie
  vom Konzept gefordert, nur über zwei separate statt einem verknüpften
  Ereignis.
- Ein Vier-Augen-Prinzip für Force-Unlock (4.3) ist damit noch nicht
  verdrahtet - folgt mit dem generischen Approval-Mechanismus in P6-S4.
- `based_on_version_number` wird sowohl am `DocumentLock` als auch an jeder
  `DocumentVersion` gespeichert, obwohl der Lock-Wert aktuell nicht für die
  Konflikterkennung ausgewertet wird (die basiert rein auf dem beim Check-in
  übergebenen Wert) - er dient der Nachvollziehbarkeit ("worauf basierte diese
  Sperre") und könnte in einer späteren Session für striktere Prüfungen
  (Check-in ohne aktive Sperre ablehnen) herangezogen werden, falls sich das
  als nötig erweist.
