# 0007 — Frontend-i18n: JSON-Wörterbuch + React-Context, nur Deutsch aktiv

**Status:** akzeptiert
**Kontext:** Konzept 8 ("Anpassbarkeit"), Session P4-S3 (nachträglich für beide bestehenden Frontend-Apps ergänzt)

## Entscheidung

Beide Frontend-Apps (`apps/user-ui`, `apps/admin-ui`) bekommen ein identisches, minimales i18n-Grundgerüst: alle sichtbaren Texte liegen in einer Sprachdatei (`src/i18n/de.json`, verschachtelte Schlüssel wie `login.heading`), ein `I18nProvider`/`useI18n()`-Kontext (`src/i18n/index.tsx`) löst `t("bereich.schlüssel")`-Aufrufe zur Laufzeit gegen die aktuell aktive Sprache auf. Komponenten enthalten keine hartkodierten deutschen Strings mehr, mit Ausnahme von Next.js' `metadata`-Export (siehe Begründung). **Aktiv ist ausschließlich Deutsch** (`defaultLocale = "de"`, keine Sprachumschaltung in der UI) — das Grundgerüst ist bewusst so gebaut, dass eine zweite Sprache später ohne Komponenten-Änderungen ergänzt werden kann.

## Begründung

- **Warum jetzt, nicht erst bei Bedarf**: Auf ausdrücklichen Wunsch vorgezogen — Strings nachträglich aus bereits gewachsenen Komponenten zu extrahieren wird mit jeder weiteren UI-Session (Reviewer-UI, Migrations-Konsole, Konzept 8/14) teurer. Ein Grundgerüst ohne i18n-Vorbereitung hätte hier einen technischen Rückstand angelegt.
- **JSON-Wörterbuch + eigener Kontext statt einer Bibliothek** (z. B. `next-intl`, `react-i18next`): Die gängigen Next.js-i18n-Lösungen sind auf Routing-basierte Sprachumschaltung (`/de/...`, `/en/...`) oder Middleware ausgelegt — beides unvereinbar mit dem statischen Export ohne Node-Laufzeitserver (ADR 0006). Eine reine Client-Context-Lösung mit statischem Dictionary-Import braucht keine Server-Komponente und keine dynamischen Routen, passt also exakt zum bestehenden CSR-Modell.
- **Eine Sprachdatei pro App statt einer geteilten i18n-Lib**: Konsistent mit der bereits getroffenen Entscheidung, dass Frontend-Apps keine gemeinsame Fachlogik importieren (ADR 0006, `auth-context.tsx` ist ebenfalls je App dupliziert) — die Wörterbücher unterscheiden sich ohnehin vollständig zwischen User- und Admin-UI (unterschiedliche Bereiche, unterschiedliche Begriffe).
- **`metadata`-Export bleibt ein direkter JSON-Import statt `useI18n()`**: Next.js wertet `export const metadata` serverseitig/zur Build-Zeit aus, bevor irgendeine Client-Komponente (und damit der Context) existiert — ein Re-Export aus dem als `"use client"` markierten i18n-Modul brach den `_not-found`-Seiten-Build (`Cannot read properties of undefined`). Layouts importieren `de.json` daher direkt, alle interaktiven Komponenten weiterhin über `useI18n()`.

## Konsequenzen

- Eine zweite Sprache hinzuzufügen heißt: neue JSON-Datei anlegen (z. B. `en.json`, gleiche Schlüsselstruktur), in `dictionaries` registrieren, `Locale`-Typ erweitern — keine Komponente muss angefasst werden, da alle ausschließlich über `t()`-Pfade sprechen.
- Es gibt noch **keine Sprachumschaltung in der UI** (kein Locale-Switcher, keine Persistierung einer Nutzerpräferenz) — `I18nProvider` erhält `locale` aktuell nur als optionale Prop mit Default `"de"`. Folgt, sobald tatsächlich eine zweite Sprache existiert; bis dahin wäre ein Switcher ohne Alternative sinnlos.
- Fehlt ein Schlüssel im Wörterbuch, gibt `t()` den Pfad selbst zurück (z. B. `"login.heading"` statt eines Textes) statt zu crashen — bewusst defensiv für ein einzelnes, noch kleines Wörterbuch; bei wachsendem Umfang wäre ein Build-Zeit-Check auf fehlende Schlüssel (z. B. via Skript oder Testfall pro Sprachdatei) sinnvoll, aber noch nicht Teil dieser Session.
- Übersetzungen für zukünftige Backend-seitig erzeugte Texte (z. B. Fehlermeldungen aus `HTTPException(detail=...)`) sind davon unberührt — die Backends liefern weiterhin deutsche Klartexte, die 1:1 in `error-text`-Feldern angezeigt werden. Eine vollständige Internationalisierung müsste diese Texte ebenfalls strukturiert (z. B. Fehlercodes statt Klartext) ausliefern — bewusst nicht Teil dieser Session, da die Backends kein internes i18n-Konzept haben und das Konzept dies auch nicht für die API-Schicht fordert.
