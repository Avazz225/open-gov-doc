# 0034 — Migration Service: direktes Installations-Paar + generische Connector-Service-Tasks in workflow-service

**Status:** akzeptiert
**Kontext:** P12-S2 (Konzept 7.2, "Migration/Transfer"). Ein Transfer läuft zwischen zwei
**Installationen** dieser Software (Sperren → Kopieren ins Zielsystem → Verifizieren → Freigabe
im Zielsystem → Löschung im Quellsystem nach Übergangsfrist) und muss **selbst als
auditierbarer, resumable Workflow über die Workflow Engine laufen** ("nicht als Sonderfall
daneben") — anders als `archival-service` (5.6), das denselben Ablauf bewusst über einen
Poll-Loop statt echtem BPMN abbildet, weil 5.6 diese Vorgabe nicht macht. Bei P12-S0 wurde als
echter Fund notiert: `workflow-service` hatte noch keine Automatic/Service-Task-Konnektor-
Aufruf-Plumbing (7.1 nennt "Auslösen eines Connector-Aufrufs" als Beispiel für einen Service
Task) — diese Session musste sie bauen, generisch statt migrationsspezifisch. Rückfrage bei
Sessionstart: 7.2 nennt anders als 7.4 (Federation Hub) keine vermittelnde Instanz — Nutzer
entschied sich für ein **direktes Installations-Paar mit API-Key** statt Hub-Vermittlung.

## Entscheidung

**Generische `connector_call`-Service-Tasks in `workflow-service`** (`spiff_adapter.py`): ein
`bpmn:serviceTask` mit `camunda:properties` `taskType=connector_call`/`serviceUrl=...` wird über
`OVERRIDE_PARSER_CLASSES` (von SpiffWorkflows `BpmnParser` selbst dokumentierter
Erweiterungspunkt) auf eine eigene `ConnectorServiceTask`-Spec-Klasse gemappt, die bei
`_execute()` einen modul-weiten, per `register_connector_task_handler()` injizierbaren Callback
aufruft. `main.py` registriert dafür einen Handler, der synchron `httpx.post(serviceUrl,
json=task.data)` ausführt und die JSON-Antwort in die Prozessdaten zurückmerged. `serviceUrl`
unterstützt `{platzhalter}`-Substitution aus den aktuellen Prozessdaten (`str.format(**data)`),
damit z. B. eine pro Instanz unterschiedliche `transfer_id` in die URL einfließen kann, ohne die
BPMN-Datei pro Instanz individuell erzeugen zu müssen. Komplett generisch — `workflow-service`
kennt `migration-service` nicht, jeder künftige Service kann einen automatischen BPMN-Schritt
treiben.

**Resumability über `POST /instances/{id}/retry`** (ebenfalls generisch): ein fehlgeschlagener
`connector_call` versetzt den Task nach `ERROR` (SpiffWorkflow-eigene Semantik). Der neue
Endpunkt setzt `ERROR`-Tasks über `reset_branch()` zurück auf `READY`/`FUTURE` und lässt
`do_engine_steps()` erneut laufen — unter Beibehaltung der bisherigen Task-Daten. `start_instance`/
`complete_task`/`retry_instance` in `repository.py` persistieren den `workflow_state`-Blob dafür
jeweils in einem `try`/`finally` (nicht erst nach erfolgreichem Abschluss) — sonst gäbe es bei
einem Fehlschlag des allerersten Schritts gar keine Instanz-Zeile zum Fortsetzen.

**Caller-bestimmte Instanz-ID** (`ProcessInstanceCreate.instance_id`, optional): derselbe
Beweggrund wie bei `federation-hub-service`s `handover_id` (ADR 0028) — ein Aufrufer, der die ID
bereits vor dem eigentlichen Start persistieren will, braucht eine im Voraus bekannte ID. Ohne
diese hätte `migration-service` bei einem Fehlschlag des allerersten automatischen Schritts
(z. B. "Sperren" nicht erreichbar) keine Möglichkeit gehabt, die dennoch in `workflow-service`
angelegte Instanz für einen späteren `/retry`-Aufruf wiederzufinden — real aufgetreten, bevor
dieser Ablauf umgestellt wurde.

**Timer-Ausdruck statt statischem Literal für die Löschfrist**: `migration-service`s
`bpmn:intermediateCatchEvent`-`timeDuration` referenziert die Prozessvariable
`retention_duration` (`retention_duration` als Bare-Identifier statt eines gequoteten
ISO-8601-Literals) — SpiffWorkflows `DurationTimerEventDefinition.has_fired()` evaluiert
`self.expression` über den Script-Engine, real verifiziert. Die bereits bestehende SLA-Poll-
Schleife (`_sla_poll_loop`/`repository.advance_timers`, P6-S2) lässt fällige Timer für **jede**
laufende Instanz unabhängig vom Prozesstyp feuern — keine neue Poll-Infrastruktur nötig.

**Direktes Installations-Paar statt Hub** (`migration-service`): `paired_installation`
(`id`, `display_name`, `base_url`, `api_key`) wird — anders als `federation-hub-service`s
`Installation`, die nur einen Hash speichert — im **Klartext** gespeichert: diese Installation
muss den Key sowohl beim ausgehenden Aufruf als Quelle präsentieren als auch beim eingehenden
Aufruf als Ziel verifizieren (`hmac.compare_digest`, konstante Zeit) — ein reiner Hash würde die
erste Rolle unmöglich machen. `POST /paired-installations` generiert bei fehlendem `api_key`
einen neuen (einmalig zurückgegeben, analog `federation-hub-service`s `POST /installations`),
oder übernimmt einen von der Gegenseite bereits ausgegebenen Key unverändert.

**`asyncio.to_thread()` für jeden `DmsTreeClient`/`PeerClient`-Aufruf** (beide synchron, siehe
`dms_client.py`): ein synchroner HTTP-Aufruf direkt in einem `async def`-Endpoint blockiert den
gesamten Event-Loop-Thread. Beim Selbst-Loopback-Test (dieselbe Instanz ruft sich selbst als Ziel
auf) führt das zu einem echten Deadlock — der blockierende Aufruf wartet auf eine Antwort von
genau dem Thread, den er selbst blockiert und der die eingehende Anfrage sonst verarbeiten würde.
Real aufgetreten (`httpx.ReadTimeout`), behoben durch `asyncio.to_thread()` (sync-Arbeit AUS
einem async-Kontext heraus auslagern — die unproblematische Richtung, anders als das in P12-S1
bewusst vermiedene `asgiref.async_to_sync`).

**Explizites `session.commit()` in jedem Schritt-Endpunkt**: `Depends(get_session)` liefert pro
Anfrage eine neue `AsyncSession`; schließt FastAPI sie am Ende einer Anfrage ohne vorherigen
`commit()`, wird eine nur geflushte Transaktion automatisch zurückgerollt. Real aufgetreten: alle
fünf Schritt-Endpunkte (`lock`/`copy`/`verify`/`release`/`delete-source`) riefen ursprünglich nur
`_mark()`s internes `flush()` auf, nie `commit()` — der gesamte Transfer lief dadurch scheinbar
fehlerfrei durch (jeder Schritt antwortete 200), aber **keine** der Statusänderungen wurde
tatsächlich persistiert (die Transfer-Zeile blieb für immer bei `"pending"` stehen).

## Begründung

- **Generische Connector-Service-Tasks statt migrationsspezifischer Sonderlösung**: der P12-S0-
  Fund bezog sich ausdrücklich auf 7.1 (Workflow Engine allgemein), nicht auf 7.2 — eine in
  `workflow-service` fest verdrahtete Kenntnis von `migration-service` wäre eine Abkürzung
  gewesen, die den nächsten Anwendungsfall (z. B. Aussonderung, die laut 5.6 "technisch eng
  verwandt" mit 7.2 ist) wieder vor genau demselben Problem stehen ließe.
- **Direktes Paar statt Hub**: 7.4 beschreibt sich selbst als Ergänzung zur "reinen Migration
  (7.2, die einen einmaligen, endgültigen Transfer beschreibt)" — ein Hub wäre für einen
  einmaligen, von einem Admin explizit konfigurierten Vorgang unnötige Vermittlungs-Infrastruktur.
- **Klartext-API-Key statt Hash**: einzig, weil diese Installation den Key selbst aktiv
  präsentieren muss (Quellrolle) — ein Hash hätte dafür keinen Sicherheitsgewinn geboten (das
  Geheimnis müsste ohnehin irgendwo im Klartext greifbar sein), nur die Zielrollen-Prüfung
  unnötig erschwert.
- **`asyncio.to_thread()` statt Dokumentation als Performance-Grenze**: ursprünglich als reine
  Performance-Abwägung eingeplant (siehe P12-S1s ähnliche Fälle) — der Selbst-Loopback-Test
  deckte auf, dass es hier keine bloße Abwägung, sondern ein echter Deadlock ist, sobald Quelle
  und Ziel dieselbe Prozess-/Event-Loop-Instanz sind.

## Konsequenzen

- **Bewusste Grenze: keine historischen Zeitstempel für migrierte Versionen** — `document-
  service`s `POST /documents/{id}/versions` setzt `created_at`/`created_by` serverseitig, kein
  Parameter zum Überschreiben. Migrierte Versionen tragen auf der Ziel-Installation den
  Migrationszeitpunkt, nicht das Original. Ein "historischer Import"-Zugang wäre ein
  eigenständiges, riskantes Feature (potenzielle Audit-Trail-Verwässerung) und ist bewusst nicht
  Teil dieser Session.
- **Bewusste Grenze: `principal_id` bleibt opak bei kopierten Berechtigungen** — kein
  Identitätsabgleich zwischen den Nutzerpopulationen zweier Installationen (7.4s Grundsatz
  "jede Installation bleibt bezüglich ihrer eigenen Daten autonom" gilt analog). Funktioniert
  korrekt, wenn beide Installationen dieselbe Nutzerbasis teilen, sonst müssen Rollen nach der
  Migration manuell nachgepflegt werden.
- **Bewusste Grenze: nur die aktuelle Dokumentversion wird migriert**, nicht die volle
  Versionshistorie — eine Schleife über alle historischen Versionen wäre möglich gewesen, wurde
  aber angesichts der ohnehin fehlenden Zeitstempeltreue (s. o.) für eine Referenzimplementierung
  zurückgestellt.
- **Bewusste Grenze: `dry-run-check` prüft nur Erreichbarkeit/Existenz des Zielordners**, keine
  vollständige Objekttyp-/Constraint-Kompatibilitätsanalyse — 7.2 nennt Letzteres als Beispiel
  ("z. B. passende Objekttypen vorhanden?"), eine vollständige Schema-Vergleichs-Engine wäre ein
  eigenständiges, großes Feature.
- **Selbst-Loopback-Test statt echter Zwei-Installationen-Test** — gleiche, bereits bei
  `federation-hub-service` etablierte und dokumentierte Grenze (ein zweiter unabhängiger Stack ist
  im Sandbox nicht sinnvoll aufsetzbar).
- **`asyncio.to_thread()` gilt jetzt als Präzedenzfall**: jeder künftige Service, der synchrone
  SDK-Aufrufe (z. B. `dms-connector-sdk`, für den geplanten CMIS-Connector P12-S4) aus `async
  def`-FastAPI-Endpunkten heraus tätigt UND dabei potenziell sich selbst aufrufen kann
  (Selbst-Loopback oder echte Zwei-Wege-Installationspaare), muss dasselbe Muster anwenden.
