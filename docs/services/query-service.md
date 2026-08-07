# query-service

**Verantwortung:** Zentrale Query- & Trace-Konsole (6.1) — eine föderierte, RBAC-gefilterte Lesezugriffsschicht auf die Read-Modelle anderer Services (P8-S1), seit P8-S2 zusätzlich die Manipulationsseite: Schutzschalter, Dry-Run, optionales/zwingendes Vier-Augen-Prinzip für einen kuratierten Katalog struktureller Manipulations-Aktionen, seit **P8-S2b** vollständig an die Admin-UI angebunden, seit **P8-S3** zusätzlich über das CLI-Tool (6.2, `dms query ...`) nutzbar — siehe [`docs/tools/cli.md`](../tools/cli.md).

**Konzept-Referenz:** 6.1
**Eigenes Postgres-Schema:** `query` (seit P8-S2, Tabelle `manipulation_mode_status` — Schutzschalter-Zustand, genuin eigener Zustand, keine Duplikation fremder Read-Modelle). Die Leseseite (P8-S1) bleibt zustandslos.

## Architekturentscheidungen

- **`pglast` (GPL-3.0-or-later) wird nicht mitgeliefert** — bei P8-S0 geklärt ([ADR 0031](../adr/0031-query-konsole-pglast-plugin.md)). `query_service/parser.py` definiert nur die Schnittstelle (`ParserPlugin.parse(query_text) -> ParsedQuery`, `load_parser_plugin(module_path)`); eine echte Implementierung (z. B. auf Basis von `pglast`) lebt vollständig außerhalb dieses Repos, exakt wie die KDBX-Plugin-Entscheidung in ADR 0029. Getestet wird die Lade-/Ausführungsmechanik über ein test-only Fake-Plugin (`tests/fixtures/fake_parser_plugin.py`), das explizit **keine** echte SQL-Grammatik ist.
- **Zweigleisige Abfragesprache statt Alles-oder-Nichts**: `GET /query/events` (strukturierte Filterparameter, kein Parser nötig) liefert den vollen Kernnutzen der Konsole sofort, auch ohne installiertes Plugin. `POST /query` (freier, psql-artiger Text) liefert `501`, solange `DMS_QUERY_PARSER_PLUGIN_MODULE` nicht gesetzt ist. Anders als bei KDBX (ein Nischen-Exportformat) wäre eine komplett funktionslose Konsole bis zur Plugin-Installation unverhältnismäßig.
- **Nur `audit-service` als Datenquelle in dieser Session** — "Tabelle" `events`. Konzept 6.1 nennt auch Reporting-/Monitoring-Read-Modelle als mögliche Quellen; diese sind strukturell additiv nachrüstbar (neue "Tabelle" im selben Verteiler), aber nicht Teil von P8-S1, da nur audit-service bereits eine generische Filter-API hat.
- **RBAC-/Bereichssperren-Filterung der Ergebniszeilen** (Konzept 6.1 wörtlich: "eine Query kann nie mehr sehen ... als die ausführende Person ohnehin dürfte") — eine Lücke, die weder `audit-service`s rohes `GET /events` noch `reporting-service`s bestehender Forensik-Trace-Endpunkt schließen (beide sind heute komplett ungefiltert/unauthentifiziert). `filtering.py` löst pro Ereignis eine Ordner-`resource_id` auf: `document-service`-Ereignisse tragen die `document_id` als `subject`, aufgelöst über `GET /documents/{id}` → `folder_id`; `folder-service`-Ereignisse tragen die `resource_id` bereits direkt als `subject`. Alle anderen Kategorien (workflow/case/auth/signature/notification/registry/permission-auf-Nicht-Ordner/...) sind **nicht auflösbar** und werden fail-closed ausgeblendet — außer für den aktivierten Superuser (4.6, die einzige im Konzept vorgesehene Ausnahme). Bewusste Umfangsgrenze, keine stille Lücke: eine generische Objekt-Berechtigung für jede denkbare Domäne existiert nicht.
- **`permission-service`s `POST /check/batch` vereinigt RBAC und Bereichssperren (4.7) bereits in einer Antwort** — 1:1 wiederverwendetes Muster aus `search-service`. Aufgelöste `resource_id`s werden dedupliziert und parallel (`asyncio.gather`) angefragt.
- **`admin.query_console`-Rolle existierte bereits, aber unbenutzt** — `permission-service`s `DOMAIN_ADMIN_ROLES`-Katalog enthält den Eintrag `domain-admin-query-console` seit dem ursprünglichen Rollen-Seeding. `query-service` ist der erste Konsument, der diese Berechtigung tatsächlich prüft (`_require_query_console`, identisches Gate-Muster wie `workflow-service._require_object_config`).
- **Superuser-Ausnahme ohne Header-Shortcut** — `AuthServiceClient.get_active_superuser()` fragt `auth-service`s `GET /superuser/status` ab; nur wer selbst der gerade aktive Superuser-Principal ist, bekommt die Sonderrechte (nicht jeder Aufrufer während irgendeine Aktivierung läuft).
- **Keine eigene Datenhaltung (Leseseite)** — `audit-service` ist bereits die autoritative Audit-Quelle; eine lokale Kopie wäre reine Duplikation (gleiche Begründung wie bei `reporting-service`s Forensik-Trace). Protokollierung (Konzept-Punkt 5, "Vollständige Protokollierung") läuft ausschließlich über einen selbst-publizierten `query.executed`-Event (exaktes Selbst-Audit-Muster wie `reporting.forensic_trace.queried`), landet über das neue `"query.>"`-Subject in `audit-service`s Kette.
- **Manipulations-Scope bewusst auf strukturierte, kuratierte Aktionen begrenzt** (seit P8-S2, per `AskUserQuestion` bestätigt) — Konzept 6.1s eigenes Beispiel ("setze Attribut Y auf allen Dokumenten vom Typ Z mit Bedingung B zurück") setzt einen filterbasierten Bulk-Write voraus, den kein Owner-Service anbietet (nur explizite ID-basierte Endpunkte existieren irgendwo im System). Ein echtes generisches SQL-Manipulationssystem wäre ein eigenständiges, mehrere Sessions umfassendes Projekt. Stattdessen: ein kleiner, in `manipulation.py` hartkodierter Katalog von drei Aktionen (`document.attribute_reset`, `permission.role_assignment.delete`, `object_type.update`), jede zielt auf ein einzelnes Objekt per ID über einen bereits existierenden Owner-Service-Endpunkt. Freier SQL-Manipulationstext bleibt wie beim Lesezugriff ein späterer Ausbau.
- **Schutzschalter als eigener, leichterer Mechanismus statt Wiederverwendung des Superuser-Break-Glass** — Konzept 6.1 nennt ihn nur "vergleichbar" mit 4.6, nicht identisch. Eigene, feingranulare Berechtigung `admin.query_console.manipulate` (getrennt von der Lese-Berechtigung `admin.query_console`), lazy Ablauf-Prüfung ohne Poll-Loop (ein abgelaufener Schutzschalter blockiert nur den nächsten Schreibversuch, es gibt nichts aufzuräumen — anders als Break-Glass, dessen Ablauf einen Keycloak-Account deaktivieren muss). Der aktivierte Superuser umgeht den Schutzschalter vollständig ("kann uneingeschränkt lesen und schreiben"), muss ihn nicht separat aktivieren.
- **Kritisch-Markierung hartkodiert, nicht konfigurierbar** — `ManipulationAction.is_critical` ist eine Python-Konstante je Aktion, keine Datenbank-/API-Einstellung. Wörtliche Umsetzung von Konzept-Punkt 4 ("lässt sich nicht durch eine abweichende allgemeine Konfiguration umgehen"): am sichersten, wenn es überhaupt keinen Konfigurationsknopf dafür gibt.
- **Vier-Augen vollständig über die bestehende ADR-0022-Infrastruktur** (`permission-service`, P6-S4) — keine Parallelstruktur. Für nicht-kritische Aktionen fragt `execute` `GET /approval-config/{action_type}` ab (Installation kann es über die bereits existierende `PUT /approval-config/{action_type}` konfigurieren); kritische Aktionen erzwingen immer eine Approval-Request, unabhängig vom Konfigurationswert — auch für den aktivierten Superuser (die einzige Stelle, an der der Superuser nicht uneingeschränkt agiert). Ausführung nach Genehmigung über einen neuen NATS-Consumer auf `permission.approval.approved` (query-service hatte bis P8-S2 nur einen Producer-Bus), identisches Muster wie `document-service`/`auth-service`.
- **Dry-Run-Token bewusst zustandslos statt einer DB-Tabelle** — ein HMAC-signierter, kurzlebiger Token (`dry_run_tokens.py`, `DMS_DRY_RUN_SECRET`) transportiert `action_type`/`params`/`principal_id`/Ablaufzeit selbst; `/manipulate/execute` verifiziert nur die Signatur, keine zweite Tabelle nötig. Der Token wird **nur beim Erstellen der Approval-Request geprüft**, nicht erneut beim asynchronen Ausführen später (Approvals können beliebig lange pending bleiben, exakt wie bei jeder bestehenden ADR-0022-Aktion).

## API

| Methode | Pfad | Beschreibung |
|---|---|---|
| `GET` | `/query/events?actor=&subject=&event_type=&since=&until=&limit=` | Strukturierte Filter-API, kein Parser-Plugin nötig. Rollen-gegatet (`admin.query_console` oder aktivierter Superuser), Ergebnis RBAC-/scope-lock-gefiltert. Antwort: `{events, total_before_filter, total_after_filter, superuser}` |
| `POST` | `/query` `{query_text}` | Freier, psql-artiger Abfragetext — `501`, solange kein Parser-Plugin konfiguriert ist (ADR 0031); sonst `400` bei ungültigem Text/unbekannter Tabelle, sonst dieselbe Filter-/Audit-Pipeline wie oben |
| `POST` | `/manipulation-mode/activate` `{duration_minutes}` | Schutzschalter aktivieren (seit P8-S2) — verlangt `admin.query_console.manipulate` oder Superuser |
| `POST` | `/manipulation-mode/deactivate` | Schutzschalter deaktivieren |
| `GET` | `/manipulation-mode/status` | `{active, activated_by, expires_at}` |
| `POST` | `/manipulate/dry-run` `{action_type, params}` | Simuliert eine Manipulations-Aktion (verpflichtend, auch für den Superuser), liefert `{preview, is_critical, dry_run_token}` |
| `POST` | `/manipulate/execute` `{dry_run_token}` | Führt die zuvor per Dry-Run geprüfte Aktion aus — sofort (`{status: "executed", result}`) oder als Approval-Request (`{status: "pending_approval", approval_request_id}`), abhängig von Kritikalität/Konfiguration |
| `GET` | `/healthz` | Health-Check |

`X-DMS-Principal` wird vom Gateway aus dem Bearer-Token injiziert (kein eigener JWT-Check nötig, gleiches Vertrauensmodell wie alle anderen Backend-Services).

## Manipulationsmodus (6.1, seit P8-S2)

Kuratierter Aktionskatalog (`manipulation.py`), jede Aktion definiert `dry_run(params) -> Vorschau-Text` und `execute(params) -> Ergebnis`:

| `action_type` | Kritisch | Owner-Endpunkt | Konzept-Kategorie |
|---|---|---|---|
| `document.attribute_reset` | Nein | `PATCH /documents/{id}` (document-service) | — (regulärer Dokumentinhalt) |
| `permission.role_assignment.delete` | Ja | `DELETE /role-assignments/{id}` (permission-service) | "Berechtigungs-/Rollentabellen" |
| `object_type.update` (nur `naming_constraints`/`conditions`) | Ja | `PUT /object-types/{id}` (object-type-service) | "Objekttyp-/Constraint-Definitionen" |

"Lizenzdaten" (dritte im Konzept genannte Kategorie) existiert nicht — kein License Service vor Phase 9.

**Ablauf**: `POST /manipulation-mode/activate` (Superuser ausgenommen) → `POST /manipulate/dry-run` (verpflichtend für alle, liefert Vorschau + Token) → `POST /manipulate/execute` mit dem Token → je nach Kritikalität/Konfiguration sofortige Ausführung oder `pending_approval` → bei Genehmigung (`POST /approval-requests/{id}/approve` auf `permission-service`) führt `query-service`s neuer Consumer die Aktion aus und publiziert `query.manipulation.executed`.

Die im Vorschau-Text sichtbaren Aktions-Parameter (`params`) sind objektspezifisch: `document.attribute_reset` = `{document_id, attribute_key}`; `permission.role_assignment.delete` = `{role_assignment_id}`; `object_type.update` = `{object_type_id, field, value}` (`field` ∈ `naming_constraints`\|`conditions`, sonst `400`).

## Datenmodell

- `manipulation_mode_status` (Singleton, `id=1`): `active`, `activated_by`, `expires_at`, `updated_at` — gleiches Muster wie `permission-service`s `SystemMaintenanceMode`.

## Events

**Konsumiert** (neuer Consumer-Bus seit P8-S2, `durable="query-service"`): `permission.approval.approved`, gefiltert auf die drei bekannten `action_type`-Strings — führt die Aktion aus, publiziert `query.manipulation.executed`/`query.manipulation.execution_failed`. Payload defensiv gelesen (`.get()`), gleiche ADR-0022-Konvention wie `document-service`.

**Publiziert** (Producer-Bus, Stream `query`):

| event_type | payload |
|---|---|
| `query.executed` | `{source: "structured"\|"sql", params, total_before_filter, total_after_filter}` — jede ausgeführte Abfrage, `actor` = ausführender Principal (Konzept-Punkt 5, unconditional, kein Abschalten möglich) |
| `query.manipulation.executed` | `{action_type, params, result}` (seit P8-S2) |
| `query.manipulation.execution_failed` | `{action_type, params}` (seit P8-S2, z. B. wenn das Zielobjekt zwischen Genehmigung und Ausführung bereits anderweitig entfernt wurde) |

**`audit-service`-Anbindung**: `audit_service/settings.py`s `subjects`-Liste um `"query.>"` ergänzt — ohne diese Ergänzung würde `query.executed` nie im Audit-Trail ankommen (derselbe Fehlertyp, der beim P7-S2-Live-Test für `"folder.>"` real gefunden wurde, hier proaktiv vermieden).

## Selbst-Registrierung (Konzept 3.2a)

Registriert sich beim Start selbst bei der Registry (`libs/dms-registry-client`), identisches Muster wie jeder andere Service. `gateway-service` routet dynamisch über `service_type` (`InstanceResolver`) — keine Gateway-Code-Änderung nötig, um `query-service` unter `/api/query-service/...` erreichbar zu machen.

## Tests

`uv run pytest services/query-service/tests`: `test_parser.py`, `test_filtering.py`, `test_api.py` (aus P8-S1, s. o.), seit P8-S2 zusätzlich `test_dry_run_tokens.py` (Ausstellen/Dekodieren, falsches Secret, manipulierte Payload, abgelaufener Token), `test_manipulation_mode.py` (Aktivieren/Deaktivieren/Ablauf), `test_manipulation.py` (alle drei Aktionen: Dry-Run-Vorschau, Execute, Objekttyp-Feld-Whitelist), `test_consumer.py` (Approval-Event triggert Ausführung, unbekannter `action_type` ignoriert, defensives Payload-Lesen, Fehlerfall publiziert `execution_failed`), `test_api.py` erweitert (Schutzschalter-Gate, Dry-Run→Execute ohne/mit konfiguriertem Vier-Augen, **kritische Aktion erzwingt Vier-Augen auch für den aktivierten Superuser** — der zentrale Sicherheitstest dieser Session, ungültiger Dry-Run-Token). **54 Tests** (vorher 24, 30 neu), alle grün, `ruff check`/`ruff format` clean.

## Offene Punkte

- **Nur `events` (audit-service) als Datenquelle** — Reporting-/Monitoring-Read-Modelle (von 6.1 ebenfalls genannt) sind additiv nachrüstbar, aber nicht Teil dieser Session.
- **RBAC-Filterung deckt nur Dokument-/Ordner-Ereignisse ab** (s. o.) — alle anderen Kategorien sind für Nicht-Superuser fail-closed unsichtbar, kein generischer Mechanismus für beliebige Domänen.
- **`reporting-service`s Forensik-Trace hat dieselbe RBAC-Lücke, wurde aber in dieser Session nicht retrofittet** — zu großer Nebenschauplatz, ggf. spätere Session (`reporting-service` könnte künftig dieselbe `filtering.py`-Logik übernehmen).
- **Kein Freitext-SQL-Manipulationstext** — nur der kuratierte, hartkodierte Aktionskatalog (s. o.); ein echtes filterbasiertes SQL-Manipulationssystem bräuchte neue Bulk-Endpunkte in mehreren Owner-Services, kein Teil dieser Session.
- **Admin-UI-Anbindung der Manipulationsseite seit P8-S2b erledigt** — `ManipulationSection` in `apps/admin-ui`, siehe `docs/services/admin-ui.md` "Query-Konsole". Seit **P8-S3** zusätzlich `dms query ...` im CLI-Tool (6.2), dieselben Endpunkte, siehe `docs/tools/cli.md`.
- **Kein Ablehnen-Button in der Admin-UI** (seit P8-S2b) — nur Genehmigen ist angebunden; Ablehnen ist bereits generisch über `permission-service`s `POST /approval-requests/{id}/reject` möglich, aber ohne UI-Anbindung in dieser Session.
- **`object_type.update` nur für zwei Felder** (`naming_constraints`/`conditions`) — bewusste Whitelist, keine beliebige `ObjectType`-Feld-Manipulation über diese Aktion.
