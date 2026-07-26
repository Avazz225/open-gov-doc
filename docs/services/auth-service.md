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
| `GET` | `/healthz` | Eigener Health-Check |

## Realm-/Client-Bootstrap

Bei jedem Start (`ensure_realm_and_client`, idempotent via `skip_exists=True`):
- Realm `dms`
- Confidential Client `dms-api` mit `directAccessGrantsEnabled=true`, `standardFlowEnabled=false` (kein Browser-Redirect-Flow in dieser Session)
- Audience-Mapper, damit `aud` im Access-Token `dms-api` statt nur `account` enthält (Keycloak-Default ohne Mapper)

**Bekannte Grenze**: `skip_exists=True` verhindert, dass eine spätere Änderung der Client-Konfiguration (z. B. neue Mapper) auf einen bereits bestehenden Client nachgezogen wird — für Dev/Test unkritisch, für Produktivbetrieb bei Konfigurationsänderungen zu beachten.

## Events

Noch keine — Login/Logout-Audit-Events (Konzept 5.3, 5.5 Session-Fingerprinting) sind nicht Teil dieser Session.

## Sensoren (Konzept 10.1)

Noch keine — folgt in Phase 11.

## Offene Punkte

- **AD-Gruppe → interne Rolle Mapping** (Konzept 4.4): Keycloak deckt lokale + LDAP/AD-föderierte Nutzer bereits nativ ab, aber die konfigurierbare Mapping-Regelengine (AD-Gruppe → DMS-Rolle) ist nicht implementiert. Rollenzuweisung/-auswertung ist Aufgabe des Permission Service (4.1, P2-S2); `/me` liefert aktuell nur Keycloaks rohe `realm_access.roles`.
- **Issuer-Hostname-Konsistenz**: Der Auth Service spricht Keycloak intern über `DMS_KEYCLOAK_BASE_URL` (im Compose-Netz `http://keycloak:8080`) an; ausgestellte Tokens tragen entsprechend `iss=http://keycloak:8080/realms/dms`. Sobald ein browserbasierter Redirect-Flow (`standardFlowEnabled`) hinzukommt, muss die vom Browser sichtbare Keycloak-URL (`http://localhost:8080`) und die interne Service-zu-Service-URL konsistent gehalten werden (Keycloak-Hostname-Konfiguration) — für die aktuelle reine Password-Grant-Nutzung nicht relevant, da immer derselbe interne Pfad verwendet wird.
- **SAML 2.0** (Konzept 4.4, für ADFS-Alt-Föderationen) nicht Teil dieser Session.
