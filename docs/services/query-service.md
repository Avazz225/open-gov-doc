# query-service

**Verantwortung:** Zentrale Query- & Trace-Konsole (6.1, seit P8-S1) — eine föderierte, RBAC-gefilterte Lesezugriffsschicht auf die Read-Modelle anderer Services. In dieser Session ausschließlich **Lesezugriff**: Manipulationsmodus + alle Sicherungsstufen (Dry-Run, Vier-Augen, kritische Tabellen) folgen in P8-S2, das CLI-Tool (6.2) in P8-S3.

**Konzept-Referenz:** 6.1
**Eigenes Postgres-Schema:** keines — der Service ist vollständig zustandslos (gleiches Muster wie `gateway-service`), Protokollierung läuft ausschließlich über den Event-Bus (s. u.).

## Architekturentscheidungen

- **`pglast` (GPL-3.0-or-later) wird nicht mitgeliefert** — bei P8-S0 geklärt ([ADR 0031](../adr/0031-query-konsole-pglast-plugin.md)). `query_service/parser.py` definiert nur die Schnittstelle (`ParserPlugin.parse(query_text) -> ParsedQuery`, `load_parser_plugin(module_path)`); eine echte Implementierung (z. B. auf Basis von `pglast`) lebt vollständig außerhalb dieses Repos, exakt wie die KDBX-Plugin-Entscheidung in ADR 0029. Getestet wird die Lade-/Ausführungsmechanik über ein test-only Fake-Plugin (`tests/fixtures/fake_parser_plugin.py`), das explizit **keine** echte SQL-Grammatik ist.
- **Zweigleisige Abfragesprache statt Alles-oder-Nichts**: `GET /query/events` (strukturierte Filterparameter, kein Parser nötig) liefert den vollen Kernnutzen der Konsole sofort, auch ohne installiertes Plugin. `POST /query` (freier, psql-artiger Text) liefert `501`, solange `DMS_QUERY_PARSER_PLUGIN_MODULE` nicht gesetzt ist. Anders als bei KDBX (ein Nischen-Exportformat) wäre eine komplett funktionslose Konsole bis zur Plugin-Installation unverhältnismäßig.
- **Nur `audit-service` als Datenquelle in dieser Session** — "Tabelle" `events`. Konzept 6.1 nennt auch Reporting-/Monitoring-Read-Modelle als mögliche Quellen; diese sind strukturell additiv nachrüstbar (neue "Tabelle" im selben Verteiler), aber nicht Teil von P8-S1, da nur audit-service bereits eine generische Filter-API hat.
- **RBAC-/Bereichssperren-Filterung der Ergebniszeilen** (Konzept 6.1 wörtlich: "eine Query kann nie mehr sehen ... als die ausführende Person ohnehin dürfte") — eine Lücke, die weder `audit-service`s rohes `GET /events` noch `reporting-service`s bestehender Forensik-Trace-Endpunkt schließen (beide sind heute komplett ungefiltert/unauthentifiziert). `filtering.py` löst pro Ereignis eine Ordner-`resource_id` auf: `document-service`-Ereignisse tragen die `document_id` als `subject`, aufgelöst über `GET /documents/{id}` → `folder_id`; `folder-service`-Ereignisse tragen die `resource_id` bereits direkt als `subject`. Alle anderen Kategorien (workflow/case/auth/signature/notification/registry/permission-auf-Nicht-Ordner/...) sind **nicht auflösbar** und werden fail-closed ausgeblendet — außer für den aktivierten Superuser (4.6, die einzige im Konzept vorgesehene Ausnahme). Bewusste Umfangsgrenze, keine stille Lücke: eine generische Objekt-Berechtigung für jede denkbare Domäne existiert nicht.
- **`permission-service`s `POST /check/batch` vereinigt RBAC und Bereichssperren (4.7) bereits in einer Antwort** — 1:1 wiederverwendetes Muster aus `search-service`. Aufgelöste `resource_id`s werden dedupliziert und parallel (`asyncio.gather`) angefragt.
- **`admin.query_console`-Rolle existierte bereits, aber unbenutzt** — `permission-service`s `DOMAIN_ADMIN_ROLES`-Katalog enthält den Eintrag `domain-admin-query-console` seit dem ursprünglichen Rollen-Seeding. `query-service` ist der erste Konsument, der diese Berechtigung tatsächlich prüft (`_require_query_console`, identisches Gate-Muster wie `workflow-service._require_object_config`).
- **Superuser-Ausnahme ohne Header-Shortcut** — `AuthServiceClient.get_active_superuser()` fragt `auth-service`s `GET /superuser/status` ab; nur wer selbst der gerade aktive Superuser-Principal ist, bekommt die Sonderrechte (nicht jeder Aufrufer während irgendeine Aktivierung läuft).
- **Keine eigene Datenhaltung** — `audit-service` ist bereits die autoritative Audit-Quelle; eine lokale Kopie wäre reine Duplikation (gleiche Begründung wie bei `reporting-service`s Forensik-Trace). Protokollierung (Konzept-Punkt 5, "Vollständige Protokollierung") läuft ausschließlich über einen selbst-publizierten `query.executed`-Event (exaktes Selbst-Audit-Muster wie `reporting.forensic_trace.queried`), landet über das neue `"query.>"`-Subject in `audit-service`s Kette.

## API

| Methode | Pfad | Beschreibung |
|---|---|---|
| `GET` | `/query/events?actor=&subject=&event_type=&since=&until=&limit=` | Strukturierte Filter-API, kein Parser-Plugin nötig. Rollen-gegatet (`admin.query_console` oder aktivierter Superuser), Ergebnis RBAC-/scope-lock-gefiltert. Antwort: `{events, total_before_filter, total_after_filter, superuser}` |
| `POST` | `/query` `{query_text}` | Freier, psql-artiger Abfragetext — `501`, solange kein Parser-Plugin konfiguriert ist (ADR 0031); sonst `400` bei ungültigem Text/unbekannter Tabelle, sonst dieselbe Filter-/Audit-Pipeline wie oben |
| `GET` | `/healthz` | Health-Check |

`X-DMS-Principal` wird vom Gateway aus dem Bearer-Token injiziert (kein eigener JWT-Check nötig, gleiches Vertrauensmodell wie alle anderen Backend-Services).

## Datenmodell

Keines — vollständig zustandsloser Service, siehe oben.

## Events

**Konsumiert:** keine (kein Consumer-Bus).

**Publiziert** (eigener Producer-Bus, Stream `query`):

| event_type | payload |
|---|---|
| `query.executed` | `{source: "structured"\|"sql", params, total_before_filter, total_after_filter}` — jede ausgeführte Abfrage, `actor` = ausführender Principal (Konzept-Punkt 5, unconditional, kein Abschalten möglich) |

**`audit-service`-Anbindung**: `audit_service/settings.py`s `subjects`-Liste um `"query.>"` ergänzt — ohne diese Ergänzung würde `query.executed` nie im Audit-Trail ankommen (derselbe Fehlertyp, der beim P7-S2-Live-Test für `"folder.>"` real gefunden wurde, hier proaktiv vermieden).

## Selbst-Registrierung (Konzept 3.2a)

Registriert sich beim Start selbst bei der Registry (`libs/dms-registry-client`), identisches Muster wie jeder andere Service. `gateway-service` routet dynamisch über `service_type` (`InstanceResolver`) — keine Gateway-Code-Änderung nötig, um `query-service` unter `/api/query-service/...` erreichbar zu machen.

## Tests

`uv run pytest services/query-service/tests`: `test_parser.py` (Lade-Erfolg/-Fehler des Plugin-Mechanismus, Fake-Plugin-Parsing inkl. `ParserPluginError` bei ungültigem Text), `test_filtering.py` (Dokument-auflösbar+erlaubt/verboten, Ordner-direkt, unauflösbar+Superuser/nicht-Superuser, fehlendes Dokument, Deduplizierung mehrfach referenzierter Dokument-IDs), `test_api.py` (403 ohne Rolle/Superuser, 200 mit Rolle, Ergebnis korrekt gefiltert, Superuser-Bypass nur für den tatsächlich aktiven Principal, `/query` 501 ohne Plugin/200 mit konfiguriertem Fake-Plugin/400 bei kaputtem Text oder unbekannter Tabelle). **24 Tests**, alle grün, `ruff check`/`ruff format` clean.

## Offene Punkte

- **Nur `events` (audit-service) als Datenquelle** — Reporting-/Monitoring-Read-Modelle (von 6.1 ebenfalls genannt) sind additiv nachrüstbar, aber nicht Teil dieser Session.
- **RBAC-Filterung deckt nur Dokument-/Ordner-Ereignisse ab** (s. o.) — alle anderen Kategorien sind für Nicht-Superuser fail-closed unsichtbar, kein generischer Mechanismus für beliebige Domänen.
- **`reporting-service`s Forensik-Trace hat dieselbe RBAC-Lücke, wurde aber in dieser Session nicht retrofittet** — zu großer Nebenschauplatz für P8-S1, ggf. spätere Session (`reporting-service` könnte künftig dieselbe `filtering.py`-Logik übernehmen).
- **Kein Freitext-SQL-Feld in der Admin-UI** — der `POST /query`-Pfad bleibt ohne installiertes Parser-Plugin ohnehin ungenutzt, siehe `docs/services/admin-ui.md`.
- **Manipulationsmodus (Dry-Run, Vier-Augen, kritische Tabellen) folgt in P8-S2**, das CLI-Tool (6.2) in P8-S3.
