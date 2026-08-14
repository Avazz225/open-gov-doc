# 0093 — AD-Gruppe→Rolle-Mapping: eigene schlanke Tabelle in auth-service, Scope-Cut auf einfache 1:1-Zuordnung

**Status:** akzeptiert (Post-Roadmap Phase 24 Session 2)
**Kontext:** Konzept 4.4 ("Gruppenmitgliedschaften aus AD werden auf interne Rollen gemappt"), betrifft `auth-service`

## Entscheidung

1. **Neue, eigene Tabelle `ad_group_role_mapping`** (`auth`-Schema, `id`, `ad_group_name`, `role_name`,
   `created_at`, `created_by`) statt Wiederverwendung/Erweiterung von `permission-service`s
   `Group`/`GroupMembership`/`RoleAssignment` (Post-Roadmap Phase 22 Session 2, ADR 0088). Jene bilden
   ADMIN-ANGELEGTE Gruppen mit expliziter, synchron zu haltender Mitgliedschaftstabelle ab — eine andere,
   unabhängige Funktion als das hier umzusetzende Mapping EXTERNER Keycloak-/AD-Gruppenclaims. Die neue
   Tabelle lebt bewusst lokal in `auth-service` (kleiner Blast-Radius, keine Kopplung an
   `permission-service`s Gruppenmaschinerie).
2. **Explizite Scope-Einschränkung gegenüber Konzept 4.4**: nur einfache 1:1-Zuordnung (eine
   `ad_group_name` → eine `role_name`) — Konzept 4.4 beschreibt als volle Zielausbaustufe zusätzlich
   zusammengesetzte Regeln ("AD-Gruppe X **und** Attribut Y → Rolle Z", "mehrere AD-Gruppen → eine
   gemeinsame Rolle"). Diese Session implementiert ausdrücklich NUR die einfache Variante, keine
   generische Regel-DSL — siehe "Konsequenzen"/`docs/services/auth-service.md` "Offene Punkte".
3. **Dynamische Auswertung bei jeder `/me`-Anfrage**, kein Caching/keine eigene Mitgliedschaftstabelle:
   Keycloak liefert die aktuellen Gruppenmitgliedschaften eines Principals ohnehin bei jedem Tokenbezug
   frisch über den `groups`-JWT-Claim — `ad_group_mapping.resolve_roles_for_groups` liest bei jedem
   Aufruf direkt gegen die Mapping-Tabelle, eine Änderung/ein Löschen einer Zuordnung wirkt sich also
   ohne Invalidierungsproblem ab dem nächsten `/me`-Aufruf aus.
4. **`groups`-Claim fehlte im Access-Token bislang komplett** — Keycloak trägt Gruppenmitgliedschaften
   nicht automatisch ein (anders als Rollen über `realm_access.roles`). Neuer Protocol-Mapper
   `oidc-group-membership-mapper` (`bootstrap._ensure_groups_mapper`, `full.path=false` — nur der blanke
   Gruppenname, kein Keycloak-interner Pfad, passend zur einfachen Namensabbildung), läuft wie
   `_ensure_client_updated` bei JEDEM Start (nicht nur bei Client-Ersteinrichtung), da `skip_exists=True`
   bei `create_client` einen bereits bestehenden Client sonst nie um den neuen Mapper ergänzen würde.
5. **`realm_roles` in `GET /me` bleibt EIN Feld** — aus dem Gruppenclaim abgeleitete Rollen werden in
   dieselbe Liste gemerged statt in ein separates `group_derived_roles`-Feld, dedupliziert. Siehe
   "Begründung".
6. **CRUD-Endpunkte (`GET`/`POST`/`DELETE /ad-group-mappings`) gegated auf `admin.user_management`** —
   dieselbe Capability/Domäne wie `GET /users`/`POST /realm-roles` ("Nutzer-/Rechteverwaltung"), keine
   neue, feingranularere Capability für diese Session.
7. **Audit über den bestehenden Event-Bus-Mechanismus**: `auth.ad_group_role_mapping.created`/`.deleted`
   (`actor=` aufrufender Principal) — `audit-service` konsumiert bereits das gesamte `auth.>`-Subject
   (seit P6-S5), kein neuer Audit-Mechanismus nötig. `created_by`/`created_at` zusätzlich direkt an der
   Zeile für einen schnellen Blick ohne Audit-Trail-Abfrage.

## Begründung

- **Warum keine generische Regel-DSL in dieser Session**: Konzept 4.4 selbst führt zusammengesetzte
  Regeln nur als Beispiel einer möglichen Zielausbaustufe an ("ebenso wie komplexere Regeln"), ohne ein
  konkretes Regelformat vorzugeben — eine tragfähige, admin-editierbare DSL für boolesche
  Gruppen-/Attribut-Kombinationen ist ein eigenständiger, deutlich größerer Entwurfsaufwand (Editor-UI,
  Validierung, Auswertungsreihenfolge bei widersprüchlichen Regeln) und explizit als eigene, spätere
  Session vorgesehen (dieser Task-Auftrag selbst benennt den Scope-Cut). Die einfache 1:1-Zuordnung deckt
  den mit Abstand häufigsten AD-Anwendungsfall bereits ab (eine AD-Gruppe pro Abteilung/Rolle) und ist in
  einer Session vollständig reviewbar.
- **Warum eine neue, eigene Tabelle statt `permission-service`s `Group`/`GroupMembership`**: Eine
  admin-angelegte `Group` (ADR 0088) braucht explizite `GroupMembership`-Zeilen, die synchron zu einer
  externen Quelle (AD/Keycloak) gehalten werden müssten, um dieselbe Funktion abzubilden — das wäre ein
  Synchronisationsproblem (Konzept 4.4 nennt selbst ein "konfigurierbares Synchronisationsintervall" für
  AD-Nutzer-/Gruppenabgleich, hier bewusst NICHT mitimplementiert, siehe "Konsequenzen"). Die
  claim-basierte Auswertung bei jedem Tokenbezug braucht dagegen gar keine Mitgliedschaftstabelle —
  Keycloak/AD bleibt alleinige Quelle der Wahrheit für "wer ist in welcher Gruppe".
- **Warum `realm_roles` gemerged statt eines separaten Felds**: Jeder bestehende Aufrufer von `GET /me`
  (Frontend-Rollenprüfungen, `permission-service`s Rollenzuweisungs-Abgleich) liest bereits `realm_roles`
  als vollständige Rollenliste eines Principals — ein zusätzliches Feld hätte JEDEN dieser Aufrufer zu
  einer Änderung gezwungen, um die neue Rollenquelle überhaupt zu berücksichtigen. Aus Sicht des übrigen
  Systems soll eine Rolle unabhängig davon gleich wirken, ob sie direkt als Keycloak-Realm-Rolle
  zugewiesen oder über eine Gruppenmitgliedschaft abgeleitet wurde.
- **Warum `admin.user_management` statt einer neuen Capability**: Diese Session fügt bewusst keinen
  neuen Berechtigungsnamen hinzu, um die Blast-Radius-Vorgabe einzuhalten — eine falsch konfigurierte
  Zuordnung kann Nutzern stillschweigend zusätzliche Rollen verleihen, gehört also klar in dieselbe
  sicherheitsrelevante Domäne wie Nutzer-/Rechteverwaltung selbst. Eine feingranularere Capability bleibt
  eine spätere, nicht-blockierende Erweiterung.

## Konsequenzen

- **Zusammengesetzte Regeln (Gruppe UND Attribut, mehrere Gruppen → eine Rolle) bleiben unimplementiert**
  — Konzept 4.4 explizit nicht vollständig abgedeckt, dokumentierter offener Punkt.
- **Kein "konfigurierbares Default-Verhalten bei nicht gemappten AD-Gruppen"** (Konzept 4.4 nennt
  explizit "keine Rolle vergeben vs. definierte Standardrolle" als Konfigurationsoption) — diese Session
  implementiert nur das erste Verhalten (keine Rolle), fest verdrahtet, keine Einstellung dafür.
- **Kein "kein Live-Editing mit sofortiger Breitenwirkung ohne Kontrolle"-Freigabe-Schritt** (Konzept 4.4:
  "Änderungen am Mapping wirken sich erst nach expliziter Freigabe/Speicherung aus") — diese Session
  macht jede Änderung sofort wirksam (Speichern = Freigabe), kein zusätzlicher Vier-Augen-Schritt wie bei
  `permission.role_assignment.create` (ADR 0060). Dokumentierter offener Punkt, keine bewusste
  Sicherheitslücke (die Änderung selbst bleibt bereits durch `admin.user_management` gegated und
  auditiert).
- **Kein AD-Synchronisationsintervall/keine Nutzer-/Gruppen-Synchronisation** (Konzept 4.4, letzter
  Absatz) — diese Session liest Gruppenmitgliedschaften ausschließlich aus dem JWT-`groups`-Claim zum
  Zeitpunkt des Tokenbezugs, kein periodischer Abgleich, keine eigene Nutzer-/Gruppentabelle.
- **JSON-Konfigurationsexport (Konzept 4.4: "Teil des JSON-Konfigurationsexports (7.3)")** nicht
  Teil dieser Session — `ad_group_role_mapping`-Zeilen sind aktuell nicht Teil von `config-service`s
  Konfigurationspaketen, ein Mapping lässt sich also nicht zwischen Installationen übertragen wie im
  Konzept vorgesehen.
- **`skip_exists=True`-Grenze bleibt** (bereits dokumentiert für den Audience-Mapper): Änderungen am
  neuen `groups`-Mapper selbst (z. B. später `full.path=true`) würden auf einem bereits bestehenden
  Client nicht automatisch nachgezogen — für Dev/Test unkritisch, `_ensure_groups_mapper` prüft nur auf
  Existenz eines Mappers namens `groups`, nicht auf dessen Konfigurationsinhalt.
