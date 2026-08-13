# 0069 — Rückwärts-Identitätsauflösung (UUID → Nutzername)

**Status:** akzeptiert (Session 4 von 11, siehe Phase 19 in `IMPLEMENTATION_PLAN.md`)
**Kontext:** Post-Roadmap Phase 19 Session 4, betrifft `auth-service`, `apps/user-ui`

## Entscheidung

Neuer Endpunkt `GET /users/{user_id}` in `auth-service` — Gegenstück zum bestehenden `GET
/users/lookup?username=` (Name → UUID, seit P14-S6). `principal_id`-Felder sind überall im System die
Keycloak-`sub`-UUID (Delegationen, Teamspace-Mitgliederlisten, `X-DMS-Principal`) — kein Nutzer kennt sie
auswendig, Frontends zeigten sie bislang roh an, mangels einer Rückwärtsauflösung.

1. **`admin_users.find_user_by_id(admin, user_id)`** (neu) — spiegelt `find_user_by_username`, nutzt
   `KeycloakAdmin.get_user(user_id)` (Einzel-Abruf, nicht `get_users(query=...)`), fängt
   `KeycloakGetError` mit `response_code == 404` ab und liefert `None`. Gleiche minimale
   `{id, username}`-Antwortform wie die Vorwärtsauflösung.
2. **`GET /users/{user_id}`** (neu, `main.py`) — gleiches Gate wie `GET /users/lookup`
   (`_require_permission(user, "users.lookup", ...)`, "everyone"-Gruppe aus ADR 0067/0068): dieselbe
   Vertrauensstufe, nur die Suchrichtung ist umgekehrt, kein neues Berechtigungswort nötig.
   **Registrierungsreihenfolge beachtet**: muss nach allen statischen `/users/...`-Pfaden
   (`/users/lookup`, `/users/directory`, `/users/count`) stehen, da FastAPI/Starlette Routen in
   Registrierungsreihenfolge matcht — ein früher registriertes `/users/{user_id}` hätte sie sonst
   verdeckt. Direkt neben `DELETE /users/{user_id}` platziert (symmetrisch, beide adressieren ein
   einzelnes Konto per ID).
3. **`apps/user-ui/src/lib/api.ts`**: neue `lookupUserById(token, userId)`-Funktion, gleiche
   Konventionen wie `lookupUserByUsername` (gleiches `UserLookup`-Interface, `request()`-Helfer).
4. **Neuer Hook `apps/user-ui/src/lib/usePrincipalNames.ts`**: löst eine Liste roher `principal_id`s in
   Nutzernamen auf, mit einfachem In-Memory-Cache über den Hook-Aufruf hinweg (keine erneute Anfrage für
   bereits aufgelöste IDs) und Fallback auf die rohe UUID bei einem Fehlschlag (z. B. `users.lookup`
   entzogen, oder ein zwischenzeitlich gelöschtes Konto) — blockiert die Anzeige nie.
5. **`DelegationsPane.tsx`/`TeamspacesPane.tsx`** nutzen den neuen Hook, um `deputy_principal_id`/
   `delegator_principal_id`/`member.principal_id` als aufgelösten Namen statt roher UUID anzuzeigen.

## Begründung

- **Warum keine neue Berechtigung, sondern Wiederverwendung von `users.lookup`**: die Rückwärtsauflösung
  ist konzeptionell dieselbe Operation wie die Vorwärtsauflösung (ein bekannter Identifikator wird in
  einen öffentlichen Nutzernamen übersetzt) - eine zweite, separate Berechtigung hätte keinen
  zusätzlichen Sicherheitswert geboten, nur mehr Verwaltungsaufwand für Admins.
- **Warum ein eigener Hook statt Inline-Logik in beiden Komponenten**: `DelegationsPane` und
  `TeamspacesPane` brauchen exakt dasselbe Verhalten (Liste roher IDs → aufgelöste Namen, Cache,
  Fehlschlag-Fallback) - eine dritte Kopie derselben ~20 Zeilen wäre reine Duplikation.
- **Warum Fallback auf die rohe UUID statt eines Ladeindikators/Fehlertexts**: die Anzeige einer
  Delegation/Mitgliedschaft darf nicht dadurch blockiert werden, dass eine einzelne
  Namensauflösung fehlschlägt (Netzwerkfehler, entzogene Berechtigung, gelöschtes Konto) - die rohe UUID
  ist im schlimmsten Fall genauso informativ wie der bisherige Ist-Zustand, nie schlechter.
- **Warum kein Batch-Endpunkt** (`POST /users/resolve` o. ä.) **statt N Einzelaufrufen**: die
  betroffenen Listen (eigene Delegationen, Teamspace-Mitglieder) sind in der Praxis klein (typischerweise
  einstellig) - ein Batch-Endpunkt wäre Overengineering für den aktuellen Umfang, kann bei Bedarf später
  nachgezogen werden, ohne den Hook selbst zu ändern (reiner Implementierungsdetail-Austausch).

## Konsequenzen

- **Tests**: `auth-service` 96 (vorher 92, +4: Positiv-/Negativ-Pfad für `GET /users/{id}`, gleiche
  Struktur wie die `GET /users/lookup`-Tests). `apps/user-ui` 169 Vitest-Tests grün (+2 neue, je ein
  Auflösungstest in `delegations-pane.test.tsx`/`teamspaces-pane.test.tsx`) - bestehende Tests bleiben
  unverändert gültig, da `lookupUserById` in ihnen standardmäßig fehlschlägt (nicht gemockt) und der Hook
  in diesem Fall auf die rohe ID zurückfällt, exakt das bisher erwartete Verhalten. `tsc --noEmit`,
  `eslint .`, `next build` clean.
- **Ein Bug bei der Live-Verifikation gefunden und sofort behoben** (kein Code-Fehler, ein
  Deployment-Schritt vergessen): der erste Live-Check gegen den Gateway lieferte `405 Method Not
  Allowed` für `GET /users/{id}` - `auth-service`s Docker-Image war noch nicht neu gebaut (reiner
  Restart übernimmt Code-Änderungen nicht, wiederholt sich als Lektion aus P18-S3/P19-S3). Nach
  `docker compose build auth-service` funktionierte der Aufruf wie erwartet.
- **Vollständig live gegen den echten laufenden Stack verifiziert** (nach Image-Neubau): `GET
  /users/lookup` liefert die eigene ID von `users-admin`, `GET /users/{id}` löst dieselbe ID zurück in
  `{"id": ..., "username": "users-admin"}` auf, ein unbekannter UUID liefert `404`. Alle drei
  vorbestehenden statischen `/users/...`-Routen (`count`, `lookup`, `directory`) funktionieren
  unverändert - keine Verdeckung durch die neue `/users/{user_id}`-Route bestätigt.
- **Keine Browser-Verifikation dieser Session möglich**: diese Sandbox-Umgebung hat kein
  Browser-Automatisierungswerkzeug (kein Playwright/Chromium) verfügbar - die Frontend-Korrektheit
  stützt sich auf `tsc`/`eslint`/`vitest`/`next build` (alle grün) plus die oben beschriebene
  Live-Verifikation des Backend-Vertrags, den `lookupUserById` tatsächlich aufruft. Keine tatsächliche
  Anzeige im Browser bestätigt - eine spätere Session mit Browser-Zugriff sollte das nachholen.
- Doku: `docs/services/user-ui.md`s "Offene Punkte"-Bullet zur rohen `principal_id`-Anzeige und
  `docs/services/permission-service.md`s "Ich vertrete"-Bullet als behoben markiert,
  `docs/services/auth-service.md`s API-Tabelle um `GET /users/{user_id}` ergänzt.
