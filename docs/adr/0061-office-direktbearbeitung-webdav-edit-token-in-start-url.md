# 0061 — Office-Direktbearbeitung: kurzlebiges WebDAV-Edit-Token in der Office-URI-Startadresse

**Status:** akzeptiert
**Kontext:** Ad-hoc Post-Roadmap-Feature (Nutzeranfrage nach Abschluss der 107-Session-Roadmap), betrifft `document-service`, `webdav-connector`, `user-ui`

## Entscheidung

Ein Klick auf ein Office-Dokument (`PreviewPane.tsx`) fordert einen neuen, kurzlebigen `WebdavEditToken`
(`POST /documents/{id}/webdav-edit-tokens`, 8h TTL) bei `document-service` an und navigiert den Browser
zu `ms-word:ofe|u|https://<token>:@<webdav-connector-host>/webdav/by-id/<document-id>.<ext>` (analog
`ms-excel:`/`ms-powerpoint:`). Das lokal installierte Office-Programm öffnet die Datei per WebDAV direkt
zum Bearbeiten; Speichern schreibt per WebDAV-`PUT` in den bereits bestehenden Check-in-Mechanismus von
`webdav-connector` zurück.

Drei Bausteine:

1. **`WebdavEditToken` (document-service)** - neue Tabelle, strukturell 1:1 an `ShareLink` angelehnt
   (`token` als PK, `expires_at`/`revoked_at`), aber zusätzlich mit `principal_id` (die Identität, die
   beim Check-in als Sperrinhaber verwendet wird - `ShareLink` braucht das nicht, da rein lesend).
   Ausstellung verlangt `document.write` (`check_write`, neu in `permission_client.py`), nicht nur
   `document.read` - ein Edit-Token gewährt Schreibzugriff.
2. **`by-id/`-Pfadauflösung (webdav-connector)** - `DmsDavProvider.get_resource_inst()` bekommt einen
   neuen, additiven Zweig vor dem bestehenden pfadbasierten `resolve_path()`: Pfade mit Präfix `by-id/`
   werden direkt über `self.tree.get_document(document_id)` aufgelöst (Methode existiert in
   `DmsTreeClient` bereits vollständig) statt über den O(Tiefe)-Pfad-Walk. Die `.ext`-Endung ist rein
   kosmetisch/für Office' Dateityperkennung und wird serverseitig verworfen.
3. **Token-als-Benutzername-Zweig (`DmsAuthDomainController.basic_auth_user`)** - ist das übergebene
   Passwort leer, wird der Benutzername als Edit-Token behandelt und gegen einen neuen, rein
   Ost-West-internen Endpunkt (`GET /internal/webdav-edit-tokens/{token}`, kein Gateway, kein
   `X-DMS-Principal`, exakt wie `DmsTreeClient` es bereits für alle anderen Aufrufe tut) aufgelöst. Bei
   Erfolg wird `environ["wsgidav.auth.user_name"]` auf die aufgelöste `principal_id` überschrieben, nicht
   das rohe Token belassen - sonst würde der Check-in fälschlich das Token statt der echten Identität als
   `created_by` verwenden. Der bestehende Benutzername+Passwort-Zweig (echter WebDAV-Mount) bleibt
   unverändert.

## Begründung

- **Warum ein Token in der URL statt eines Passwort-Dialogs**: mit dem Nutzer abgestimmt (Empfehlung
  angenommen) - Office/Windows sollen die Bearbeitung ohne manuellen Zwischenschritt starten. Das
  `token:@`-Userinfo-in-der-URL-Muster ist real genutzt (u. a. Nextcloud/ownCloud-artige "In Office
  öffnen"-Integrationen), aber in dieser Sandbox ohne echtes Windows/Office nicht abschließend
  verifizierbar, ob der Zugangsdaten-Dialog dadurch zuverlässig unterdrückt wird - dieselbe, bereits bei
  `apps/office-addin` (ADR 0045) akzeptierte Sandbox-Grenze, hier bewusst dokumentiert statt stillschweigend
  vorausgesetzt.
- **Warum `principal_id` nicht an den Client zurückgegeben wird** (`WebdavEditTokenOut` liefert nur
  `token`/`expires_at`): der Client (Browser) braucht die Identität nicht, nur `webdav-connector`
  (Ost-West) - unnötige Datenweitergabe an einen weniger vertrauenswürdigen Kontext vermeiden.
  Ausdrückliche Rechteprüfung bei jeder WebDAV-Aktion während der Sitzung erfolgt bewusst NICHT erneut -
  nur bei Ausstellung geprüft, ein zwischenzeitlicher Rechteentzug wirkt sich erst nach
  Tokenablauf/-widerruf aus. Gleiche Kategorie Einschränkung, die dieses Projekt an anderer Stelle
  (Freigabelinks) bereits so akzeptiert.
- **Warum `apps/office-addin` (ADR 0045) kein Duplikat ist**: löst ein anderes Problem (Task-Pane-Add-in
  in einem bereits geöffneten, leeren Word-Dokument, kein WebDAV, kein URI-Schema, nur Word). Dieses
  Feature startet die Bearbeitung eines EXISTIERENDEN DMS-Dokuments direkt aus dem Browser heraus, für
  alle drei Office-Formate. Komplementär, kein Ersatz.
- **Warum `webdav-connector` eine neue, dem Browser bislang nie mitgeteilte Basis-URL braucht**
  (`NEXT_PUBLIC_WEBDAV_CONNECTOR_BASE_URL`): anders als alle übrigen `api.ts`-Aufrufe, die durchgängig
  über den Gateway laufen, muss der Office-URI-Handler direkt gegen den WebDAV-Endpunkt navigieren (kein
  WebDAV-Proxying über den Gateway vorgesehen) - analoges Buildzeit-Variablen-Muster wie
  `NEXT_PUBLIC_GATEWAY_BASE_URL`.

## Konsequenzen

- **Bewusst zurückgestellt**: Admin-UI/`MetadataPanel`-Oberfläche zum Anzeigen/Widerrufen aktiver
  Edit-Tokens - der Lese-Endpunkt (`GET .../webdav-edit-tokens`) existiert bereits, die UI dafür nicht in
  dieser Session gebaut (`ShareLinkModal.tsx` ist strukturell fast identisch übertragbar).
- **Nicht in dieser Sandbox live verifizierbar**: der eigentliche Office-Start und ob der
  Zugangsdaten-Dialog unterdrückt wird (kein Windows/Office hier vorhanden) - wird als dokumentierte
  Grenze behandelt, nicht als Blocker. Der Token-Ausstellungs-/Auflösungs-Roundtrip selbst (per `curl`
  gegen `webdav-connector`) ist hingegen vollständig verifizierbar und Teil der Regression.
