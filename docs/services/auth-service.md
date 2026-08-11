# auth-service

**Verantwortung:** Schlanker OIDC-Broker vor Keycloak — hält Client-Secret und Admin-Zugang, Aufrufer sehen nur Login/Refresh/Token-Validierung (Konzept 4.4). Keine eigene IAM-Logik, keine eigene Nutzertabelle.

**Konzept-Referenz:** 4.4/2.5 (Kontakte, seit P15-S4)/7.4 (föderierte Kontaktsuche, seit P15-S4)/14.1 (Realm-Rollen für Konfigurationspakete, seit P17-S1)
**Eigenes Postgres-Schema:** `auth` (seit P15-S4, nur `federation_identity` — eine Singleton-Zeile für die optionale föderierte Kontaktsuche, siehe unten). Bis dahin war der Service vollständig zustandslos; Keycloak selbst verwaltet seine Daten weiterhin im eigenen Schema `keycloak` (siehe `infra/postgres-init/001-schemas.sql`).

## API

| Methode | Pfad | Beschreibung |
|---|---|---|
| `POST` | `/login` | `{username, password}` → Password-Grant gegen Keycloak, liefert Access-/Refresh-Token. **Seit P6-S6**: liest `X-DMS-Maintenance-Active` (vom Gateway injiziert, 4.8) — ist Wartungsmodus aktiv und `username` ungleich dem Superuser-Konto, `503` statt Login |
| `POST` | `/refresh` | `{refresh_token}` → neue Tokens |
| `GET` | `/me` | Bearer-Token validieren (JWKS, zustandslos, keine Rückfrage bei Keycloak), normalisierte Identität zurückgeben |
| `GET` | `/users` | Nutzer auflisten (seit P4-S3, Grundlage der Admin-UI-Nutzerverwaltung) — liest direkt aus Keycloak. **Seit P6-S5 gegated**: erfordert die Capability `admin.user_management` (Domäne "Nutzer-/Rechteverwaltung", 4.6), sonst `403` |
| `POST` | `/users` | Nutzer anlegen (`username`, `email`, `password`, `first_name`, `last_name`) — 409 bei bereits vergebenem Benutzernamen. Gegated wie `GET /users` |
| `DELETE` | `/users/{id}` | Nutzer löschen — 404 bei unbekannter `id`. Gegated wie `GET /users` |
| `GET` | `/me/preferences` | Theme-Präferenz des angemeldeten Kontos (`{theme}`, Default `"auto"`) — seit P4-S6 |
| `PUT` | `/me/preferences` | Theme-Präferenz setzen (`{theme}` ∈ `light`/`dark`/`high-contrast`/`auto`, sonst 422) — seit P4-S6 |
| `GET` | `/superuser/status` | Break-Glass-Status (4.6, seit P6-S5): `{active, expires_at}`, seit **P6-S6** zusätzlich `principal_id` (Keycloak-`id` des Superuser-Kontos, für den Not-Shutdown-Aufheben-Check des Permission Service, 4.8) — 404, falls das Superuser-Konto noch nicht angelegt wurde |
| `POST` | `/superuser/deactivate` | Vorzeitige, freiwillige Deaktivierung (seit P6-S5) — ergänzt die automatische Ablauf-Erzwingung über den Poll-Loop |
| `GET` | `/users/lookup` | Exakte Namensauflösung (`?username=`) — liefert nur `{id, username}` zurück, `404` bei unbekanntem Namen. Neu in P14-S6, für `teamspace-service`s Einladen-per-Nutzername (2.5): erfordert lediglich `Depends(get_current_user)` (jeder gültige Token), bewusst NICHT hinter `admin.user_management` wie `GET /users` oben — jede Person, nicht nur Domain-Admins, soll andere zu einem Team-Arbeitsbereich einladen können. Siehe [ADR 0043](../adr/0043-teamspace-service-membership-and-permission-integration.md) |
| `GET` | `/users/count` | Interner Aufruf von `license-service` (9.1 "benannte Accounts"-Modell, seit P9-S1) — ungegatet, da kein Service einen echten Keycloak-Bearer-Token für `Depends(get_current_user)` besitzt |
| `GET` | `/sessions/count` | Interner Aufruf von `license-service` (9.1 "gleichzeitige Nutzer"-Modell, seit P9-S1) — `KeycloakAdmin.get_client_sessions_stats()`, ungegatet |
| `GET` | `/users/directory?q=` | Verzeichnissuche (2.5/4.4, seit P15-S4, Keycloak-`search`-Parameter — Präfix je Feld, kein Teilstring, siehe "Kontakte" unten) — `Depends(get_current_user)` genügt (jeder gültige Token, kein `admin.user_management`-Gate) |
| `GET` | `/users/directory/federation-status` | Ob die föderierte Kontaktsuche auf dieser Installation aktiviert ist (`{enabled, peer_installation_count}`) — ungegatet, steuert die Sichtbarkeit der entsprechenden Frontend-Sektion |
| `GET` | `/users/directory/federated?q=` | Föderierte Suche über alle bekannten, für Kontaktsuche freigegebenen Peer-Installationen (2.5/7.4, seit P15-S4) — `403`, falls auf dieser Installation nicht aktiviert |
| `POST` | `/users/directory/federated-search-inbound` | Von einer Peer-Installation aufgerufen (öffentliche Route, kein `X-DMS-Principal`) — authentisiert über `X-Installation-Signature`/`X-Installation-Id`, siehe "Kontakte" unten |
| `GET` | `/realm-roles` | **Seit P17-S1** (14.1): aktuelle Keycloak-Realm-Rollen, gefiltert um Keycloak-Built-ins (`offline_access`, `uma_authorization`, `default-roles-*`) — ungegatet, liefert nur Namen (identisches Vertrauensmodell wie `permission-service`s `GET /roles`) |
| `POST` | `/realm-roles` | **Seit P17-S1**: legt die übergebenen Realm-Rollen idempotent an (`{names: [...]}`, `create_realm_role(..., skip_exists=True)`) — verlangt `X-DMS-Principal`-Header mit `admin.user_management`-Berechtigung (Service-zu-Service, kein Keycloak-JWT-Endpunkt), sonst `403`. Weist die Rolle niemandem zu, siehe "Realm-Rollen-Verwaltung" unten |
| `GET` | `/healthz` | Eigener Health-Check |

## Realm-/Client-Bootstrap

Bei jedem Start (`ensure_realm_and_client`, idempotent via `skip_exists=True`):
- Realm `dms`
- Confidential Client `dms-api` mit `directAccessGrantsEnabled=true`, `standardFlowEnabled=false` (kein Browser-Redirect-Flow in dieser Session)
- Audience-Mapper, damit `aud` im Access-Token `dms-api` statt nur `account` enthält (Keycloak-Default ohne Mapper)
- Deklariertes User-Profile-Attribut `dms_theme` (seit P4-S6, siehe unten) — ohne diese Deklaration verwirft Keycloaks Declarative User Profile das Attribut bei jedem `update_user`-Aufruf stillschweigend
- Realm-Rolle `dms-admin` (seit **P5e-S2**, `create_realm_role(..., skip_exists=True)`) — erste im System tatsächlich ausgewertete Rolle, siehe `docs/services/document-service.md` "Kennzeichengenerator" (privilegierte Änderung von `attributes["Kennzeichen"]`)
- Deklariertes User-Profile-Attribut `dms_superuser_expires_at` (seit **P6-S5**, gleiches Deklarationsmuster wie `dms_theme`) — Break-Glass-Ablaufzeitpunkt (4.6, siehe unten)
- Superuser-Konto (seit **P6-S5**, Username `superuser`) — idempotent angelegt mit `enabled=False`, siehe "Superuser Break-Glass" unten
- Technische Domain-Admin-Konten (seit **P6-S5**, `enabled=True`, Liste `DOMAIN_ADMIN_ACCOUNTS` statt Einzelkonstante seit **P6-S6**): `users-admin`/`users-admin` (Domäne "Nutzer-/Rechteverwaltung") und seit P6-S6 zusätzlich `config-admin`/`config-admin` (Domäne "Workflow-Konfiguration", 4.6/4.8-Retrofit, siehe `docs/services/workflow-service.md`) — nach Anlage folgt für jedes Konto (best-effort, siehe unten) eine Rollenzuweisung gegen `permission-service`

**Bekannte Grenze**: `skip_exists=True` verhindert, dass eine spätere Änderung der Client-Konfiguration (z. B. neue Mapper) auf einen bereits bestehenden Client nachgezogen wird — für Dev/Test unkritisch, für Produktivbetrieb bei Konfigurationsänderungen zu beachten.

## Theme-Präferenz (Konzept 8, seit P4-S6)

Cross-UI-Theming (Hell/Dunkel/Hoher-Kontrast/Automatisch, User-UI und Admin-UI) speichert seine Präferenz geräteübergreifend am Nutzerkonto statt nur lokal im Browser — Begründung und Stolpersteine (Declarative-User-Profile-Falle) in [ADR 0009](../adr/0009-cross-ui-theming-profile-persistence.md). Kurzfassung: `dms_theme` ist ein deklariertes Keycloak-Nutzerattribut, gelesen/geschrieben über den bestehenden Admin-Client (`admin_users.get_theme_preference`/`set_theme_preference`), exponiert über `/me/preferences`. Kein neuer Persistenz-Baustein nötig.

## Domänengetrennte Admin-Rollen (4.6, seit P6-S5)

Domain-Admin-"Rollen" sind bewusst **keine Keycloak-Realm-Rollen** (anders als `dms-admin`), sondern systemeigene `Role`-Zeilen in `permission-service` (siehe `docs/services/permission-service.md`) — `auth-service` erzeugt nur die zugehörigen **technischen Konten** und weist ihnen die Rolle per HTTP-Aufruf gegen `permission-service` zu (`permission_client.py`, `PermissionServiceClient.ensure_role_assignment`). Vollständige Architekturbegründung siehe [ADR 0023](../adr/0023-superuser-breakglass-and-domain-admin-accounts.md). Diese Session legt nur `users-admin` (Domäne "Nutzer-/Rechteverwaltung") tatsächlich an; die Rollenzuweisung erfolgt best-effort beim Lifespan-Start — ist `permission-service` noch nicht erreichbar, wird sie übersprungen und beim nächsten Neustart erneut versucht (kein Retry-Loop).

## Superuser Break-Glass (4.6, seit P6-S5)

Ein einzelnes, standardmäßig deaktiviertes (`enabled=False`) Keycloak-Konto `superuser`. Reaktivierung läuft **ausschließlich** über den generischen Vier-Augen-Mechanismus des Permission Service (P6-S4, ADR 0022): `POST /approval-requests` mit `action_type="auth.superuser.activate"` gegen `permission-service`, das für diesen Aktionstyp beim eigenen Start `requires_approval=True` und `required_permission="breakglass.approve"` vorbelegt (strenger als die "irgendeine zweite Person"-Regel aus 4.3 — Initiator *und* Genehmiger müssen die Rolle `breakglass-approver` halten). Nach Genehmigung konsumiert `auth-service` (**erster NATS-Konsument dieses Service überhaupt**, `consumer.py`) das publizierte `permission.approval.approved` und aktiviert das Konto: `enabled=True` + `dms_superuser_expires_at`-Attribut (`activated_at + superuser_activation_minutes`, Default 30 min) — publiziert danach `auth.superuser.activated`.

Ein periodischer Poll-Loop (`_superuser_poll_loop`, `superuser_poll_interval_seconds`, Default 30s — exakt dasselbe Muster wie workflow-services SLA-Zeitüberwachung, [ADR 0020](../adr/0020-sla-timer-polling.md)) deaktiviert abgelaufene Aktivierungen automatisch und publiziert `auth.superuser.deactivated` (`reason="expired"`, oder `"manual"` bei `POST /superuser/deactivate`). **Bewusste Vereinfachung** (siehe ADR 0023): ein einziger absoluter Ablauf-Zeitstempel statt separater Gesamtdauer- und rollierender 10-Minuten-Inaktivitäts-Timer.

## Not-Shutdown (4.8, seit P6-S6)

`POST /login` liest den vom Gateway auf jedem proxied Request injizierten `X-DMS-Maintenance-Active`-Header (Default `"false"`, falls das Login direkt am Service statt über das Gateway aufgerufen wird — dann ist der Wartungsmodus faktisch nie wirksam, siehe `docs/services/gateway-service.md`): ist er `"true"` und der angefragte `username` ungleich `superuser.SUPERUSER_USERNAME`, wird der Login mit `503` abgelehnt, **bevor** überhaupt ein Password-Grant gegen Keycloak versucht wird — wörtliche Umsetzung von "neue Logins außer für den Superuser werden abgelehnt" (4.8). Der Superuser-Login selbst wird dadurch nicht automatisch erfolgreich — ein falsches Passwort liefert weiterhin `401`, der Header entscheidet nur, ob überhaupt versucht wird. Vollständige Architekturbegründung (Gateway als Durchsetzungspunkt, Header-Broadcast-Muster) in [ADR 0024](../adr/0024-not-shutdown-gateway-enforced.md).

## Events

**Publiziert** (`stream="auth"`, seit P6-S5): `auth.superuser.activated` (`{request_id, expires_at}`), `auth.superuser.deactivated` (`{reason}`, `"expired"`|`"manual"`).

**Konsumiert** (`durable="auth-service"`, seit P6-S5, erster Konsument dieses Service): `permission.approval.approved`, gefiltert auf `action_type="auth.superuser.activate"` — jeder andere Aktionstyp wird ignoriert (gehört einem anderen Service, gleiches Prinzip wie in ADR 0022 beschrieben).

## Kontakte (2.5/4.4/7.4, seit P15-S4)

Verzeichnis zum Auffinden anderer Mitarbeitender - "lokal, immer verfügbar" (2.5, wörtlich), optional installationsübergreifend. Vollständige Architekturbegründung: [ADR 0054](../adr/0054-kontakte-directory-independent-second-federation-identity-per-installation.md).

- **Lokale Suche**: `admin_users.search_users` nutzt Keycloaks eingebauten `search`-Query-Parameter (case-insensitive über Benutzername/Vor-/Nachname/E-Mail, serverseitig in Keycloak selbst) — kein eigener Filter-Mechanismus nötig. Antwort bewusst ohne `enabled`-Feld (Freigabestatus ist eine administrative Angelegenheit). **Per Live-Verifikation korrigiert** (ursprünglich als "Teilstring" angenommen): Keycloaks `search` matcht je Feld nur als **Präfix**, nicht an beliebiger Stelle — `q=admin` findet `config-admin` nicht, `q=config` schon (siehe ADR 0054 "Offene Punkte").
- **Föderierte Suche - eigene, zweite Federation-Hub-Identität**: `auth-service` registriert sich unabhängig von `workflow-service`s bereits bestehender Federation-Teilnahme (P6-S9/P13-S4) ein zweites Mal beim selben Hub (eigenes RSA-2048-Schlüsselpaar, eigene `installation_id`, Anzeigename-Suffix `" (Kontakte)"`) — `_ensure_federation_identity()` in `main.py`, identisches Muster wie `workflow_service.main._ensure_federation_identity`. Opt-in über `DMS_FEDERATION_HUB_BASE_URL`; zusätzlich `DMS_FEDERATED_DIRECTORY_ENABLED` muss `true` sein, damit die föderierten Endpunkte tatsächlich aktiv sind (zwei getrennte Schalter: Hub-Registrierung vs. tatsächliche Freigabe für Suchanfragen).
- **Fähigkeits-Markierung**: registriert sich mit `supported_process_types=["dms.contact-directory.v1"]` — zweckentfremdet das bereits bestehende, generische Listenfeld in `federation-hub-service`s `Installation`-Modell als Fähigkeits-Marker (`directory_federation.CONTACT_DIRECTORY_CAPABILITY`), keine Code-Änderung in federation-hub-service nötig.
- **Direkte Installation-zu-Installation-Anfragen, nicht über den Hub relayt**: `GET /users/directory/federated` fragt jede über `GET /installations` bekannte, nicht widerrufene, für Kontaktsuche freigegebene Peer-Installation DIREKT über deren `callback_base_url` an (`directory_federation.search_all_peers`/`query_peer`), signiert mit dem eigenen privaten Schlüssel (`X-Installation-Signature`, RSA-PSS/SHA-256, identisches Schema wie ADR 0039 — `federation_crypto.py`, dupliziert wie bereits zweimal zuvor in diesem Projekt, siehe ADR 0054). Ein einzelner nicht erreichbarer/ablehnender Peer blockiert die übrigen nicht.
- **`POST /users/directory/federated-search-inbound`**: empfängt eine signierte Anfrage einer Peer-Installation — verifiziert `X-Installation-Signature` gegen den beim Hub hinterlegten öffentlichen Schlüssel der ANFRAGENDEN Installation (live per `GET /installations` abgerufen, kein lokaler Peer-Schlüsselspeicher), lehnt unbekannte/widerrufene/nicht für Kontaktsuche registrierte Installationen mit `401` ab. Öffentliche Route am Gateway (`gateway-service`s `public_routes`, kein `X-DMS-Principal`), analog zu `workflow-service`s `federation/inbound`.
- **Keine Ende-zu-Ende-Verschlüsselung der Nutzlast** (anders als das Handover-Schema) — der Hub liegt bei direkten Aufrufen ohnehin nie im Anfragepfad, siehe ADR 0054 "Begründung".

## Realm-Rollen-Verwaltung (14.1, seit P17-S1)

Bis P17-S1 wurde jede Keycloak-Realm-Rolle einzeln, hart codiert im Bootstrap angelegt
(`bootstrap._ensure_dms_admin_role` für `dms-admin`) — kein genereller Weg, eine NEUE Realm-Rolle
anzulegen, ohne Code zu ändern und den Service neu zu deployen. `GET`/`POST /realm-roles`
verallgemeinert exakt dasselbe Primitiv (`create_realm_role(..., skip_exists=True)`) auf beliebige
Namen, damit ein Konfigurationspaket (`config-service`s neue `realm_roles`-Kategorie, z. B. für
`dms-poststelle`, 2.5) sie mitbringen kann, ohne einen neuen Mechanismus zu erfinden. **Bewusste
Grenze, identisch zum bestehenden `dms-admin`-Muster**: der Endpunkt legt nur die Rolle an, weist
sie niemandem zu — Zuweisung an konkrete Nutzer bleibt weiterhin außerhalb dieses Service über die
Keycloak Admin Console (siehe "Offene Punkte"). Details/Begründung siehe
[ADR 0058](../adr/0058-konfigurationspakete-manifest-realm-roles-and-gateway-import-route-split.md).

## Selbst-Registrierung (Konzept 3.2a, seit P4-S1)

Registriert sich beim Start selbst bei der Registry (`libs/dms-registry-client`: Register, periodischer Heartbeat, Deregister beim Shutdown) - Grundlage für das Routing des API-Gateways (`docs/services/gateway-service.md`). Opt-in über `DMS_REGISTRY_SERVICE_BASE_URL`/`DMS_SELF_ADDRESS`; ohne beide Werte läuft der Service unverändert ohne Discovery.

## Sensoren (Konzept 10.1)

Noch keine — folgt in Phase 11.

## Tests

`uv run pytest services/auth-service/tests` (**64 Tests**, davon 5 neu seit **P17-S1**,
`test_realm_roles.py`: `GET /realm-roles` enthält das bereits bootstrapped `dms-admin`, schließt
Keycloak-Built-ins aus, `POST /realm-roles` ohne/mit unautorisiertem Principal → `403`, legt eine
neue Rolle idempotent an — zweiter Aufruf mit demselben Namen scheitert nicht, gleiches
`authorized_principal`-Fixture-Muster wie `config-service`s `tests/conftest.py`. Davon 11 seit
**P15-S4**, `test_directory.py`): lokale Verzeichnis-Suche (Authentifizierungspflicht, Präfix-Treffer je Feld, für reguläre Nutzer verfügbar nicht nur Domain-Admins), Federation-Status-Default, `403` auf den föderierten Endpunkten ohne Aktivierung, sowie eine echte Selbst-Registrierung gegen den laufenden `federation-hub-service` (`federation_enabled`-Fixture, monkeypatcht `settings` vor einem frischen `TestClient(app)`) inkl. echtem Signaturprüfungs-Pfad (gültige/ungültige Signatur, unbekannte Installation, eigene Installation wird aus den föderierten Ergebnissen ausgeschlossen) — läuft gegen echtes Postgres/Keycloak/`federation-hub-service`, keine Mocks. **Nebenbei gefundener und behobener Bug**: `FederationHubClient.register()` übermittelte `supported_process_types` ursprünglich gar nicht an den Hub — die eigene Fähigkeits-Markierung (`dms.contact-directory.v1`) wäre dadurch nie tatsächlich im Adressbuch sichtbar gewesen, jede eingehende föderierte Anfrage (auch eine legitime) wäre mit `401` abgelehnt worden. Erst durch den echten Live-Selbst-Loopback-Test sichtbar geworden, nicht durch reines Mocking. **Zusätzlicher Befund bei der Live-Verifikation gegen den laufenden Gateway**: Keycloaks `search`-Parameter ist entgegen der ursprünglichen Annahme kein Teilstring-, sondern ein Präfix-Match je Feld — Dokumentation entsprechend korrigiert (siehe oben, ADR 0054 "Offene Punkte"). Ebenfalls beobachtet: wiederholte `federation_enabled`-Testläufe hinterlassen echte, dauerhafte Registrierungen im geteilten `federation-hub-service`-Adressbuch (kein Aufräumen möglich ohne konfigurierten `hub_operator_key`) — siehe ADR 0054 "Offene Punkte".

## Offene Punkte

- **AD-Gruppe → interne Rolle Mapping** (Konzept 4.4): Keycloak deckt lokale + LDAP/AD-föderierte Nutzer bereits nativ ab, aber die konfigurierbare Mapping-Regelengine (AD-Gruppe → DMS-Rolle) ist nicht implementiert. Rollenzuweisung/-auswertung ist Aufgabe des Permission Service (4.1, P2-S2); `/me` liefert aktuell nur Keycloaks rohe `realm_access.roles`.
- **Issuer-Hostname-Konsistenz**: Der Auth Service spricht Keycloak intern über `DMS_KEYCLOAK_BASE_URL` (im Compose-Netz `http://keycloak:8080`) an; ausgestellte Tokens tragen entsprechend `iss=http://keycloak:8080/realms/dms`. Sobald ein browserbasierter Redirect-Flow (`standardFlowEnabled`) hinzukommt, muss die vom Browser sichtbare Keycloak-URL (`http://localhost:8080`) und die interne Service-zu-Service-URL konsistent gehalten werden (Keycloak-Hostname-Konfiguration) — für die aktuelle reine Password-Grant-Nutzung nicht relevant, da immer derselbe interne Pfad verwendet wird.
- **SAML 2.0** (Konzept 4.4, für ADFS-Alt-Föderationen) nicht Teil dieser Session.
- **`/users`-Endpunkte seit P6-S5 gegated** (siehe oben) — löst den vormaligen offenen Punkt für diesen Service. Die Zuweisung von `admin.user_management` an *weitere* Principals (z. B. echte Menschen zusätzlich zum technischen `users-admin`-Konto) läuft über die jetzt selbst gegatete Nutzer-/Rechteverwaltungs-Admin-UI-Seite (`POST /role-assignments` gegen `permission-service`).
- **Keine Rollenzuweisungs-API/-UI für Keycloak-Realm-Rollen** (seit P5e-S2, seit P17-S1 nur teilweise gelöst): `dms-admin`/`dms-poststelle` etc. sind Keycloak-Realm-Rollen, kein systemeigenes `permission-service`-Konstrukt (anders als die Domain-Admin-Rollen aus P6-S5). Seit P17-S1 existiert immerhin ein genereller **Anlege**-Weg (`POST /realm-roles`, z. B. aus einem Konfigurationspaket) — die **Zuweisung** an konkrete Nutzer bleibt aber weiterhin ausschließlich über die Keycloak Admin Console, keine API/UI dafür in diesem Projekt.
- **5 der 7 Domain-Admin-Rollen aus 4.6 ohne zugeordnetes technisches Konto** (seit P6-S5/S6): `domain-admin-storage`/`-license`/`-query-console`/`-deletion`/`-deletion-vs` existieren nur als `Role`-Zeile in `permission-service`, ohne Keycloak-Konto und ohne dass irgendein Endpunkt sie prüft — folgt jeweils mit der künftigen Retrofit-Session der betreffenden Domäne. `domain-admin-config` ist seit **P6-S6** durchgesetzt (`config-admin`-Konto, `workflow-service`s Prozessdefinitions-Endpunkte).
- **Keine erhöhte Auditierungspriorität während einer aktiven Superuser-Session** (4.6, seit P6-S5): `audit-service` konsumiert die Break-Glass-Lifecycle-Events (`auth.>`) mit normaler Priorität; Fremdaktionen, die *während* der Aktivierung in anderen Services ausgeführt werden, sind nicht gesondert markiert.
- **Keine rollierende Inaktivitäts-Deaktivierung** (4.6, seit P6-S5): ein einziger absoluter Ablauf-Zeitstempel statt getrennter Gesamtdauer-/10-Minuten-Inaktivitäts-Timer, siehe ADR 0023.
- **Bug entdeckt bei P6-S6, nicht behoben (P6-S5-Code)**: das Superuser-Konto kann in der aktuellen Live-Umgebung nicht interaktiv einloggen (`POST /login` liefert `401`/"Account is not fully set up" direkt von Keycloak). Ursache: `firstName`/`lastName`/`email` fehlen am Keycloak-Konto — Keycloaks Declarative User Profile verlangt diese Felder im "user"-Kontext, `ensure_superuser_account`s Idempotenz-Guard (`if admin.get_users(...): return`) zieht sie nie nach, falls das Konto einmal (z. B. durch einen frühen Testlauf während der P6-S5-Entwicklung mit unvollständiger Payload) ohne sie angelegt wurde. Der Break-Glass-**Aktivierungsfluss** selbst (Approval-Mechanismus, `enabled`-Flag, Ablauf-Attribut) ist davon unberührt — nur ein tatsächlicher interaktiver Login als `superuser` schlägt fehl. Fix müsste `ensure_superuser_account` idempotent auf fehlende Felder prüfen und sie per `update_user` nachziehen, statt bei Existenz sofort zurückzukehren — nicht Teil dieser Session (P6-S6), da außerhalb ihres Scopes; für eine künftige Session vorgemerkt.
- **`GET /users/lookup` ist ein Existenz-Oracle** (seit P14-S6): jeder authentifizierte Nutzer kann herausfinden, ob ein bestimmter Nutzername existiert — bewusst so belassen (interne Verwaltungssoftware, bekannte Nutzerpopulation), aber eine dokumentierte Abweichung vom vorherigen Zustand (Nutzerverzeichnis komplett hinter `admin.user_management`). Siehe [ADR 0043](../adr/0043-teamspace-service-membership-and-permission-integration.md).
