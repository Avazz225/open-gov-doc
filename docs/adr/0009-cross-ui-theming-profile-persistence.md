# 0009 — Cross-UI-Theming: Keycloak-Nutzerattribut statt neuer Persistenz-Baustein

**Status:** akzeptiert
**Kontext:** Konzept 8, Session P4-S6 (Nutzer-Feedback nach dem ersten echten Browser-Test des MVP)

## Entscheidung

Hell/Dunkel/Hoher-Kontrast/Automatisch wird als Präferenz **am Nutzerkonto** gespeichert, nicht nur lokal im Browser, damit sie geräteübergreifend wirkt. Speicherort ist ein deklariertes Keycloak-Nutzerattribut (`dms_theme`) statt eines neuen Persistenz-Bausteins:

- `auth_service.bootstrap.ensure_realm_and_client` deklariert `dms_theme` idempotent im realmweiten Declarative User Profile (`_ensure_theme_attribute`), mit `permissions: {view: [admin, user], edit: [admin, user]}`.
- `auth_service.admin_users.get_theme_preference`/`set_theme_preference` lesen/schreiben das Attribut über den bestehenden Admin-Client (`build_admin_client`, seit P4-S3 für die Nutzerverwaltung vorhanden).
- Neue Endpunkte `GET/PUT /me/preferences` am Auth Service (`ThemePreference`-Schema, `Literal["light", "dark", "high-contrast", "auto"]`), self-service über den Bearer-Token (`user["sub"]` = Keycloak-User-ID), kein Admin-Aufruf durch den Client selbst nötig.
- Beide Frontends (User-UI, Admin-UI) bekommen je ein eigenes, bewusst dupliziertes `theme-context.tsx` (ADR 0006: keine gemeinsame Fachlogik zwischen unabhängig deploybaren Apps) mit `ThemeProvider`/`useTheme()`: `localStorage` (`dms.theme`) als sofort verfügbarer Cache (kein Warten auf die erste Server-Antwort, funktioniert auch auf der Login-Seite ohne Sitzung), synchronisiert mit dem Server-Attribut, sobald ein `accessToken` vorhanden ist.
- `data-theme` wird auf `document.documentElement` gesetzt (`useLayoutEffect`, um einen sichtbaren Flash des falschen Themes vor dem ersten Bildaufbau zu vermeiden) und steuert per CSS-Variablen (`--dms-bg`, `--dms-fg`, `--dms-border`, `--dms-accent`, ...) das gesamte bestehende Stylesheet beider Apps.

## Begründung

- **Warum ein Keycloak-Attribut statt eines neuen Service/einer neuen Tabelle**: Nutzerkonten leben laut Konzept bereits vollständig in Keycloak (kein eigenes Nutzer-Schema in `auth-service`, s. `docs/services/auth-service.md`). Eine reine UI-Präferenz eines bestehenden Kontos rechtfertigt keinen neuen Persistenz-Baustein - das war bereits die Prämisse aus `IMPLEMENTATION_PLAN.md` für diese Session.
- **Stolperstein Declarative User Profile**: Keycloak 25+ (Default seit dieser Version, hier verwendet) verwirft bei `update_user` jedes nicht im Realm-Profil deklarierte Attribut **stillschweigend** - kein Fehler, einfach kein Effekt. Ein erster Testlauf ohne `_ensure_theme_attribute` zeigte genau das: `PUT /me/preferences` gab 200 zurück, ein anschließendes `GET` lieferte trotzdem wieder `"auto"`. Ursache über direkten Vergleich von `admin.get_realm_users_profile()` gefunden (nur `username`/`email`/`firstName`/`lastName` deklariert). Ohne diese Deklaration wäre die gesamte Funktion klaglos wirkungslos gewesen.
- **`localStorage`-Cache trotz Server-Persistenz**: Ohne ihn müsste jede App bis zur ersten `/me/preferences`-Antwort warten, bevor ein Theme feststeht - inkl. eines sichtbaren Sprungs auf der Login-Seite (dort existiert noch kein Token). Der Cache macht das Theme sofort verfügbar; ein späterer Sync überschreibt ihn, sobald die Serverantwort da ist. Bewusste Vereinfachung: kein Konfliktauflösungsmechanismus, falls Server- und lokaler Wert divergieren (z. B. ein zweites Gerät hat zwischenzeitlich geändert) - letzter Fetch gewinnt, kein Retry bei fehlgeschlagenem `PUT`.
- **`useLayoutEffect` statt Zuweisung im Render** (anders als der `gatewayBaseUrl`-Singleton in ADR 0008): Hier gibt es keine Kindkomponente, die im *gleichen* Render-Durchlauf synchron auf den neuen Wert angewiesen ist - `useLayoutEffect` reicht aus, um das Attribut vor dem Browser-Paint zu setzen, und bleibt näher an der React-Konvention.
- **Warum vier Stufen (Hell/Dunkel/Hoher Kontrast/Automatisch) statt nur `prefers-color-scheme`**: "Hoher Kontrast" ist kein natives Browser-Farbschema und lässt sich nicht über `color-scheme` erzwingen - er braucht eigene, undurchsichtige (nicht transparente) Akzentfarben, s. `globals.css` beider Apps. "Automatisch" bleibt trotzdem der Default, um Nutzer ohne explizite Wahl nicht zu bevormunden.

## Konsequenzen

- Ein Nutzerkonto hat **eine** Theme-Präferenz, unabhängig davon, mit welcher App (User-UI/Admin-UI) oder welchem Gerät es sich anmeldet - Admin-UI-Mehrfachinstallationen (ADR 0008) haben aber pro Installation ein eigenes Konto, daher auch eine potenziell eigene Theme-Präferenz je Installation (kein gemeinsamer Wert über Installationsgrenzen hinweg, konsistent mit der dortigen Isolation).
- Der `dms.theme`-`localStorage`-Schlüssel ist in der Admin-UI bewusst **nicht** installationsspezifisch (anders als `dms.tokens.<id>` in ADR 0008) - ein Wechsel zu einer anderen Installation zeigt kurzzeitig noch die zuletzt gecachte Theme-Wahl, bis die neue Installation ihre eigene Präferenz nachgeladen hat. Akzeptierte Vereinfachung für dieses Grundgerüst, kein Korrektheitsproblem (nur ein kurzer visueller Zwischenzustand).
- Kein Retry/keine Konfliktauflösung bei fehlgeschlagenem `PUT /me/preferences` - die Auswahl gilt sofort lokal weiter, ein Fehlschlag der Server-Persistenz bleibt unbemerkt (kein UI-Fehlerhinweis). Für ein Grundgerüst ohne Multi-Device-Szenario im Testfokus akzeptiert.
- Das Theming-Verhalten selbst (Umschalten im Popover/in der Kopfzeile, kein Flash beim Neuladen) ist nur über Vitest-Komponententests verifiziert - kein Browser in dieser Entwicklungsumgebung verfügbar (siehe `docs/services/user-ui.md`).
