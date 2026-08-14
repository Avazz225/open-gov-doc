# 0092 — storage-service: Ziel-Metadaten (object_lock_mode/role) live editierbar, Ziel-Set-Struktur bleibt env-var-only

**Status:** akzeptiert (Post-Roadmap Phase 22 Session 7)
**Kontext:** Post-Roadmap Phase 22 Session 7, betrifft `storage-service`, `admin-ui`

## Entscheidung

Der Plan-Wortlaut für diese Session ("OCR-/Speicher-Ziel-Set editierbar machen — Erweiterung derselben
zwei bestehenden Seiten aus P22-S6") war mehrdeutig: "Ziel-Set" ist im bestehenden Code/der Doku
ausschließlich ein `storage-service`-Konzept, `ocr-service` hat kein Äquivalent. Vor der Umsetzung per
`AskUserQuestion` geklärt statt angenommen: Scope = **nur** `object_lock_mode`/`role` je bereits
konfiguriertem Ziel editierbar machen (Erweiterung von `StorageGuard`/`/storage-guard/`, der Seite, die
P22-S6 als sein UI-Vorbild referenzierte) — **keine** Zugangsdaten, **keine** Struktur-CRUD, **kein**
"OCR-Ziel-Set" (existiert nicht).

1. **Neue, sparse DB-Tabelle `target_override`** (`target_id` PK, `object_lock_mode`, `role`,
   `updated_at`) — anders als `OperationalConfig`/`GuardConfig` (P22-S6, Singleton mit Get-or-create)
   hat nur ein tatsächlich überschriebenes Ziel überhaupt eine Zeile; fehlt eine, gilt unverändert der
   Env-Var-Wert aus `Settings.targets`.
2. **Neuer Endpunkt `PUT /guard-status/{target_id}/config`** — `404` bei unbekannter `target_id` ("nur
   bestehende Einträge bearbeiten", gleiche Vorgabe wie P22-S6), `422` falls die Änderung KEIN reguläres
   (nicht-archiviertes) Ziel mehr übrig ließe (neu gefundene Sicherheitslücke, siehe "Begründung").
3. **`_compute_target_state()`** (neue reine Funktion in `main.py`) merged `Settings.targets` mit allen
   `target_override`-Zeilen zu einer effektiven `BackendTargetConfig`-Liste — aufgerufen beim Start
   UND bei jedem `PUT`, Ergebnis wird sofort in `app.state.target_configs`/`.targets`/`.archive_targets`/
   `.lock_target_ids` zurückgeschrieben (Live-Reload ohne dass jeder einzelne Lesezugriff im übrigen Code
   selbst neu aus der DB lesen müsste).
4. **`resolve_targets()`/`resolve_archive_targets()`** (`backends/__init__.py`) nehmen seither eine
   `list[BackendTargetConfig]` entgegen statt `Settings` direkt zu lesen — Aufrufer übergeben je nach
   Kontext die strukturelle Env-Var-Liste (Startup/Healthz) oder die live gemergte Liste (`PUT`-Handler).
5. **`admin-ui`**: `StorageGuard.tsx` (`/storage-guard/`) bekommt zwei neue Spalten mit
   Checkboxen ("Governance-Mode", "role=archive") statt der bisherigen rein lesenden "Object Lock"-Spalte
   — ein Klick ruft sofort `PUT .../config` für genau dieses Ziel auf und lädt neu.

## Begründung

- **Warum sparse statt Singleton**: `target_override` hat (anders als `OperationalConfig`) keine
  sinnvollen globalen Default-Werte, die bei der allerersten Zeile geseedet werden müssten — jedes Ziel
  ist unabhängig, die meisten Installationen werden nie einen Override setzen. Eine Zeile pro
  tatsächlich geändertem Ziel ist einfacher als ein Singleton mit einem JSON-Dict-Feld.
- **Warum `app.state` bei jedem `PUT` neu berechnet wird, statt bei jedem Lesezugriff selbst aus der DB
  zu lesen** (anders als P22-S6s `OperationalConfig`, die bei JEDEM betroffenen Request frisch gelesen
  wird): `object_lock_mode`/`role` werden an ca. 15 Stellen im Code gelesen (Upload-Routing,
  Archiv-Routing, Retention-Guard, Lock-Status-Anzeigen) — eine Änderung dieser Größenordnung an jeder
  einzelnen Stelle hätte das Risiko deutlich erhöht, eine Stelle zu übersehen. `PUT`-Zeitpunkt-
  Neuberechnung (analog zu `app.state.backends`, das ebenfalls einmalig berechnet wird) erreicht
  dasselbe Live-Reload-Ergebnis (ein einzelner In-Prozess-State ist für ALLE nachfolgenden Requests
  sofort sichtbar) mit einem deutlich kleineren, risikoärmeren Diff. Kompromiss: bei mehreren
  horizontal skalierten `storage-service`-Repliken sehen andere Instanzen die Änderung erst bei ihrem
  eigenen nächsten `PUT`-Aufruf oder Neustart — dokumentiert als bekannte Grenze (siehe "Konsequenzen").
- **Warum `PUT .../config` ablehnt, wenn dadurch kein reguläres Ziel mehr übrig bliebe** (echter, beim
  Implementieren gefundener Fehlerzustand, nicht Teil der ursprünglichen Anfrage): `upload_object`
  verwendet `app.state.targets[0]` als Primärziel — wäre `app.state.targets` nach einer
  `role="archive"`-Änderung leer (bei nur einem konfigurierten Ziel, wie im Dev-/Testaufbau), würde
  JEDER folgende Upload mit einem nicht abgefangenen `IndexError` (letztlich `500`) statt eines
  aussagekräftigen Fehlers abstürzen. Die neue Prüfung berechnet die Ziel-Liste VOR dem eigentlichen
  Schreiben probeweise und lehnt mit `422` ab, bevor der ungültige Zustand überhaupt persistiert wird.
- **Warum keine Nachziehung der Quorum-Erfüllbarkeit bei einer `role`-Änderung**: `PUT
  /operational-config` (P22-S6) validiert `quorum_count` bereits gegen die AKTUELLE Zielanzahl beim
  Setzen. Eine `role`-Änderung könnte diese Prüfung nachträglich invalidieren (weniger reguläre Ziele als
  der zuvor gesetzte `quorum_count`) — bewusst nicht in dieser Session behoben (kleinerer, selten
  eintretender Randfall als die "Ziel-Liste komplett leer"-Absturzgefahr oben), als bekannte Lücke
  dokumentiert.

## Konsequenzen

- **Migration**: keine (eine brandneue Tabelle).
- **Bekannte Grenze bei horizontaler Skalierung**: mehrere Repliken dieses Service teilen sich `app.state`
  NICHT - eine Replik, die keinen eigenen `PUT`-Aufruf oder Neustart seit einer Änderung hatte, sieht die
  neuen Werte nicht. Für die aktuelle Single-Replik-Deployment-Realität dieses Projekts unkritisch,
  dokumentiert für eine künftige Mehr-Repliken-Session.
- **Bekannte Lücke**: `quorum_count` (P22-S6) wird bei einer `role`-Änderung nicht neu gegen die
  Zielanzahl validiert (siehe "Begründung").
- **Testinfrastruktur-Fund** (proaktiv gefunden, bevor er zu einem Fehlschlag führte): `storage-service`s
  `tests/conftest.py` hatte bereits eine Teardown-`DELETE`-Liste (anders als die übrigen Services dieser
  Session, die GAR KEINE hatten) - diese Liste fehlte für `operational_config` (P22-S6) UND die neue
  `target_override`-Tabelle. Beide ergänzt, durch dreimaliges Hintereinander-Ausführen verifiziert.
- **Tests**: `storage-service` 122 (vorher 117, +5: `404` bei unbekanntem Ziel, `422` bei
  "würde kein reguläres Ziel übrig lassen" mit dem einzigen Testziel, ein Ende-zu-Ende-Beweis über ein
  VOR dem Override mit `retain_until` hochgeladenes Objekt, das danach ohne Neustart unter
  Governance-Sperre steht, plus zwei Repository-Unit-Tests). `admin-ui` 204 (vorher 201, +3:
  `storage-guard.test.tsx` um Umschalten von Object-Lock-Mode/Aussonderungs-Rolle sowie Fehleranzeige
  erweitert).
- **Live gegen den echten laufenden Stack verifiziert** (Image-Neubau + Neustart von
  `storage-service`/`admin-ui`): `PUT` auf unbekanntes Ziel → `404`; `PUT role=archive` auf `local`
  (einziges konfiguriertes reguläres Ziel im Dev-Stack) → `422`; ein echtes Objekt mit `retain_until` VOR
  Aktivierung von `object_lock_mode=governance` hochgeladen, danach Governance-Mode live aktiviert
  (`GET /guard-status` bestätigte es sofort), ein Löschversuch ohne Bypass lieferte `403` - exakt das
  Live-Reload-Verhalten ohne Neustart zwischen Upload und Sperr-Aktivierung. Governance-Mode danach
  zurückgesetzt; das Test-Objekt selbst bleibt bis zum natürlichen Ablauf seiner Aufbewahrungsfrist
  (~24h) unter dem unabhängigen, backend-typ-unabhängigen Anwendungsschicht-Guard gesperrt (kein
  `dms-admin`-Realm-Rollen-Konto für einen `bypass_governance`-Löschversuch in dieser Session verfügbar)
  — harmlos, klar als Testartefakt benannt. Kein interaktiver Browser-Test der UI-Änderung (kein
  Browser/Playwright in dieser Entwicklungsumgebung verfügbar, projektweit etablierte Praxis).
- Doku: neues [ADR 0092](0092-storage-target-metadata-editable.md), `docs/services/storage-service.md`
  (API-Tabelle, neue Sektion, Tests-Sektion, "Offene Punkte"), `docs/services/admin-ui.md`
  ("Speicher-Wächter"-Sektion aktualisiert, Backend-Anbindungstabelle, Tests-Sektion) ergänzt.
