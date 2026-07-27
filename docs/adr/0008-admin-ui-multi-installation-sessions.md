# 0008 — Admin-UI Multi-Installation: clientseitige Installationsliste, Sitzung je Installation

**Status:** akzeptiert
**Kontext:** Konzept 3a/8, Session P4-S5 (Nutzer-Feedback nach dem ersten echten Browser-Test des MVP)

## Entscheidung

Die Admin-UI verwaltet eine Liste konfigurierter Installationen (`{id, name, gatewayBaseUrl}`), rein clientseitig in `localStorage` (`dms.installations`, `dms.activeInstallationId`) — kein neuer Backend-Service dafür, da diese Liste eine reine UI-Präferenz ist, keine fachliche Daten (siehe Konzept 3a: eine Installation kennt nur sich selbst, es gibt keine zentrale Instanz, die "alle Installationen" kennen dürfte, außer dem optionalen, hier nicht gebauten Fleet-Management-Service).

Technisch:

- `lib/api.ts`s bisher fest importierte `GATEWAY_BASE_URL`-Konstante wird durch eine **mutable Modulvariable** (`gatewayBaseUrl`) plus Setter (`setGatewayBaseUrl()`) ersetzt. Alle bestehenden Aufrufer (`UserManagement`, `ObjectTypeEditor`, `RegistryOverview`, `auth-context.tsx`) bleiben unverändert — sie kennen nie eine URL, nur `service_type`/Pfad.
- `InstallationProvider` (`lib/installation-context.tsx`) hält die Liste + aktive Installation und ruft `setGatewayBaseUrl()` synchron im Render (nicht in einem `useEffect`) auf, da `AuthProvider` als Kindkomponente die aktuelle Adresse bereits in seinem eigenen ersten Render-Effekt braucht — Effekte feuern von unten nach oben, ein `useEffect` im `InstallationProvider` käme dafür zu spät.
- `AuthProvider` bekommt einen **eigenen `localStorage`-Schlüssel je Installation** (`dms.tokens.<installationId>` statt des bisherigen globalen `dms.tokens`) und lädt/speichert Sitzungen ausschließlich für die jeweils aktive Installation, ohne die Sitzungen anderer Installationen zu berühren.
- Provider-Reihenfolge in `layout.tsx`: `I18nProvider > InstallationProvider > AuthProvider` — `AuthProvider` braucht die aktive Installation, um seinen Storage-Schlüssel zu bilden.

## Begründung

- **Warum nicht einfach mehrere Browser-Tabs/-Profile**: Genau das war die vom Nutzer benannte Alternative, die vermieden werden sollte — "man muss sich nicht in n Stück einloggen". Eine Installationsliste mit Umschalter innerhalb einer laufenden Admin-UI-Instanz ist die im Konzept (8) explizit geforderte Lösung.
- **Kein Single-Sign-on über Installationsgrenzen hinweg**: Widerspräche der bewussten Isolation aus Konzept 3a (jede Installation hat ihre eigene, vollständig unabhängige Identitätsverwaltung/Keycloak-Realm). Jede Installation braucht daher weiterhin eine eigene, einmalige Anmeldung — die Erleichterung ist ausschließlich, dass ein späterer Wechsel *zurück* zu einer bereits angemeldeten Installation keine erneute Anmeldung erfordert, solange deren Sitzung noch gültig ist.
- **Mutable Singleton statt Context/Prop-Drilling durch `api.ts`**: `api.ts` ist ein reines Funktionsmodul, kein React-Baum — es kann keinen Context konsumieren. Jede Aufruferfunktion um einen `gatewayBaseUrl`-Parameter zu erweitern hätte jede bestehende Komponente und jeden bestehenden Testfall angefasst, für einen Fall (Installationswechsel), der pro Sitzung selten passiert. Ein einzelner Setter, der bei jedem Wechsel aufgerufen wird, ist die kleinere, weniger invasive Änderung.
- **Seiteneffekt im Render statt in `useEffect`**: bewusste, dokumentierte Abweichung von der React-Konvention "keine Seiteneffekte im Render" — hier reine Zuweisung einer externen Modulvariablen (kein DOM/keine Subscription), bei wiederholter Ausführung mit demselben Wert idempotent, daher auch unter React Strict Mode unkritisch. Die Alternative (`useEffect` im Provider) hätte eine Race Condition eingeführt: `AuthProvider`s eigener Sitzungswiederherstellungs-Effekt (Kind-Effekt, feuert zuerst) hätte in bestimmten Fällen noch gegen die *alte* Gateway-Adresse aufgerufen.

## Konsequenzen

- Installationen sind rein lokal im Browser gespeichert — kein Backup, keine Synchronisierung zwischen Geräten/Browsern eines Admin-Nutzers. Das ist eine bewusste Grenze dieses Grundgerüsts, kein vollständiges Provisioning (das wäre Aufgabe des optionalen Fleet-/Lizenz-Management-Service, Konzept 3a, Phase 13).
- Wird die zuletzt verbleibende Installation entfernt versucht, verhindert `InstallationProvider` das aktiv (`removeInstallation` ist bei genau einer verbleibenden Installation ein No-op) — es muss immer mindestens eine Installation konfiguriert bleiben, sonst hätte die gesamte übrige Admin-UI kein gültiges Gateway-Ziel mehr.
- Multi-Installation-Verhalten (Sitzungsisolation, Umschalten ohne erneute Anmeldung) ist nur über Vitest-Komponententests verifiziert (`installation-context.test.tsx`, `auth-context.test.tsx`) — kein Browser in dieser Entwicklungsumgebung verfügbar, siehe `docs/services/admin-ui.md`.
- Die User-UI hat **kein** äquivalentes Multi-Installation-Konzept — laut Konzept 8 ist das ausschließlich eine Admin-UI-Anforderung (Administratoren betreuen ggf. mehrere Installationen, Endnutzer arbeiten typischerweise nur an ihrer eigenen).
