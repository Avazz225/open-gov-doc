# auth-service

**Verantwortung:** Schlanker OIDC-Broker vor Keycloak — hält Client-Secret und Admin-Zugang, Aufrufer sehen nur Login/Refresh/Token-Validierung (Konzept 4.4). Keine eigene IAM-Logik, keine eigene Nutzertabelle.

**Konzept-Referenz:** 4.4
**Eigenes Postgres-Schema:** keins — Auth Service selbst ist zustandslos, Keycloak verwaltet seine Daten im Schema `keycloak` (siehe `infra/postgres-init/001-schemas.sql`).

## API

| Methode | Pfad | Beschreibung |
|---|---|---|
| `POST` | `/login` | `{username, password}` → Password-Grant gegen Keycloak, liefert Access-/Refresh-Token |
| `POST` | `/refresh` | `{refresh_token}` → neue Tokens |
| `GET` | `/me` | Bearer-Token validieren (JWKS, zustandslos, keine Rückfrage bei Keycloak), normalisierte Identität zurückgeben |
| `GET` | `/users` | Nutzer auflisten (seit P4-S3, Grundlage der Admin-UI-Nutzerverwaltung) — liest direkt aus Keycloak |
| `POST` | `/users` | Nutzer anlegen (`username`, `email`, `password`, `first_name`, `last_name`) — 409 bei bereits vergebenem Benutzernamen |
| `DELETE` | `/users/{id}` | Nutzer löschen — 404 bei unbekannter `id` |
| `GET` | `/me/preferences` | Theme-Präferenz des angemeldeten Kontos (`{theme}`, Default `"auto"`) — seit P4-S6 |
| `PUT` | `/me/preferences` | Theme-Präferenz setzen (`{theme}` ∈ `light`/`dark`/`high-contrast`/`auto`, sonst 422) — seit P4-S6 |
| `GET` | `/healthz` | Eigener Health-Check |

## Realm-/Client-Bootstrap

Bei jedem Start (`ensure_realm_and_client`, idempotent via `skip_exists=True`):
- Realm `dms`
- Confidential Client `dms-api` mit `directAccessGrantsEnabled=true`, `standardFlowEnabled=false` (kein Browser-Redirect-Flow in dieser Session)
- Audience-Mapper, damit `aud` im Access-Token `dms-api` statt nur `account` enthält (Keycloak-Default ohne Mapper)
- Deklariertes User-Profile-Attribut `dms_theme` (seit P4-S6, siehe unten) — ohne diese Deklaration verwirft Keycloaks Declarative User Profile das Attribut bei jedem `update_user`-Aufruf stillschweigend
- Realm-Rolle `dms-admin` (seit **P5e-S2**, `create_realm_role(..., skip_exists=True)`) — erste im System tatsächlich ausgewertete Rolle, siehe `docs/services/document-service.md` "Kennzeichengenerator" (privilegierte Änderung von `attributes["Kennzeichen"]`)

**Bekannte Grenze**: `skip_exists=True` verhindert, dass eine spätere Änderung der Client-Konfiguration (z. B. neue Mapper) auf einen bereits bestehenden Client nachgezogen wird — für Dev/Test unkritisch, für Produktivbetrieb bei Konfigurationsänderungen zu beachten.

## Theme-Präferenz (Konzept 8, seit P4-S6)

Cross-UI-Theming (Hell/Dunkel/Hoher-Kontrast/Automatisch, User-UI und Admin-UI) speichert seine Präferenz geräteübergreifend am Nutzerkonto statt nur lokal im Browser — Begründung und Stolpersteine (Declarative-User-Profile-Falle) in [ADR 0009](../adr/0009-cross-ui-theming-profile-persistence.md). Kurzfassung: `dms_theme` ist ein deklariertes Keycloak-Nutzerattribut, gelesen/geschrieben über den bestehenden Admin-Client (`admin_users.get_theme_preference`/`set_theme_preference`), exponiert über `/me/preferences`. Kein neuer Persistenz-Baustein nötig.

## Events

Noch keine — Login/Logout-Audit-Events (Konzept 5.3, 5.5 Session-Fingerprinting) sind nicht Teil dieser Session.

## Selbst-Registrierung (Konzept 3.2a, seit P4-S1)

Registriert sich beim Start selbst bei der Registry (`libs/dms-registry-client`: Register, periodischer Heartbeat, Deregister beim Shutdown) - Grundlage für das Routing des API-Gateways (`docs/services/gateway-service.md`). Opt-in über `DMS_REGISTRY_SERVICE_BASE_URL`/`DMS_SELF_ADDRESS`; ohne beide Werte läuft der Service unverändert ohne Discovery.

## Sensoren (Konzept 10.1)

Noch keine — folgt in Phase 11.

## Offene Punkte

- **AD-Gruppe → interne Rolle Mapping** (Konzept 4.4): Keycloak deckt lokale + LDAP/AD-föderierte Nutzer bereits nativ ab, aber die konfigurierbare Mapping-Regelengine (AD-Gruppe → DMS-Rolle) ist nicht implementiert. Rollenzuweisung/-auswertung ist Aufgabe des Permission Service (4.1, P2-S2); `/me` liefert aktuell nur Keycloaks rohe `realm_access.roles`.
- **Issuer-Hostname-Konsistenz**: Der Auth Service spricht Keycloak intern über `DMS_KEYCLOAK_BASE_URL` (im Compose-Netz `http://keycloak:8080`) an; ausgestellte Tokens tragen entsprechend `iss=http://keycloak:8080/realms/dms`. Sobald ein browserbasierter Redirect-Flow (`standardFlowEnabled`) hinzukommt, muss die vom Browser sichtbare Keycloak-URL (`http://localhost:8080`) und die interne Service-zu-Service-URL konsistent gehalten werden (Keycloak-Hostname-Konfiguration) — für die aktuelle reine Password-Grant-Nutzung nicht relevant, da immer derselbe interne Pfad verwendet wird.
- **SAML 2.0** (Konzept 4.4, für ADFS-Alt-Föderationen) nicht Teil dieser Session.
- **`/users`-Endpunkte ohne eigene Autorisierungsprüfung** (seit P4-S3): Wie die bereits bekannten ungated Admin-Endpunkte anderer Services (Force-Unlock, Bereichssperren) verlässt sich auch die Nutzerverwaltung auf das API-Gateway (nur Token-Gültigkeit, keine Rollenprüfung) — jeder authentifizierte Principal kann aktuell Nutzer anlegen/löschen. Reale Autorisierung ("nur Admin-Rolle") ist derselbe offene Punkt wie bei den Bereichssperren des Permission Service.
- **Keine Rollenzuweisungs-API/-UI** (seit P5e-S2): `dms-admin` wird bei jedem Start idempotent im Realm *angelegt*, aber die *Zuweisung* an konkrete Nutzer hat keinen Endpunkt/keine Admin-UI-Bedienung — muss vorerst direkt über die Keycloak Admin Console erfolgen. Die ursprüngliche Phase-5e-Planung ging von einer "bestehenden Rollenverwaltung" aus, die es bei näherer Prüfung so nicht gibt (`/users` verwaltet nur Konten, keine Rollen) — für einen einzelnen, seltenen Admin-Grant in der aktuellen Solo-/Test-Nutzung pragmatisch hingenommen, aber vor produktivem Mehrnutzerbetrieb nachzuholen.
