# Der OG Doc Admin-Assistent

Der Admin-Assistent ist eine Anleitung für einen KI-Agenten, zum Beispiel Claude Code, die ihn befähigt,
OG Doc für Sie einzurichten und Fragen zu Ihrer Installation zu beantworten. Sie sagen ihm, was Sie
brauchen, zum Beispiel:

- „Leg Frau Müller an und gib ihr Sachbearbeitung im Ordner Bauamt."
- „Alarm, wenn ein Speicher zu 80 % voll ist."
- „Was würde das eGov-Paket bei uns ändern?"
- „Warum sieht Herr Kaya den Ordner Personal nicht?"

Er liest den aktuellen Stand, erklärt, was er vorhat, zeigt eine Vorschau und ändert erst, wenn Sie
zustimmen. Danach prüft er das Ergebnis und fasst zusammen, was sich geändert hat.

## Wie er arbeitet

- **Nur über das Kommandozeilenwerkzeug `dms`.** Der Assistent nutzt dieselbe Schnittstelle, dieselben
  Berechtigungsprüfungen und denselben Prüfpfad wie die Admin-Oberfläche. Er hat keinen Zugriff an OG Doc
  vorbei, weder auf die Datenbank noch auf Keycloak.
- **In Ihrer eigenen Anmeldung.** Sie melden sich mit `dms login` an. Jede Änderung des Assistenten
  erscheint im Prüfpfad unter **Ihrem** Namen, denn Sie haben ihr zugestimmt. Es gibt kein gemeinsames
  Bot-Konto.
- **Nie mehr Rechte als Sie.** Was Ihre Rollen nicht erlauben, kann auch der Assistent nicht. Fehlt eine
  Rolle, sagt er, welche, und hört auf.
- **Lesen → Plan → Vorschau → Zustimmung → Ausführen → Prüfen.** Vorschauen gibt es für:
  - Konfigurationspakete (Vergleich mit dem aktuellen Stand);
  - Alarmregeln (aktueller Wert und Zustand);
  - Postfächer (Verbindungstest);
  - Prozessmigrationen.

## Was er nie tut

- **Vier-Augen-Anträge genehmigen.** Er zeigt Ihnen, dass ein Antrag entstanden ist und wer ihn
  freigeben kann. Die Freigabe ist immer der bewusste Schritt einer zweiten Person.
- **Unumkehrbares ohne ausdrücklichen Auftrag für genau diesen Schritt:** eine Prozessinstanz beenden,
  Rollen, Gruppen, Typen oder Benutzer löschen, Zugriffe entziehen, eine Lizenz einspielen. Ein
  allgemeines „mach einfach" genügt nicht; er nennt die Folgen und fragt.
- **Notfall- und Sonderrechte:** Superuser oder Break-Glass aktivieren, den Manipulationsmodus der
  Abfragekonsole nutzen, pseudonymisierte Daten aufdecken, die systemweite Notabschaltung auslösen,
  Dokumente herabstufen, Datensätze endgültig löschen oder vernichten. Dafür verweist er auf die
  Admin-Oberfläche.
- **Mit Notfallrechten arbeiten.** Ist Ihre Anmeldung mit Break-Glass- oder Superuser-Rechten aktiv,
  bittet er um eine normale Admin-Anmeldung.

## Einrichtung

1. **Kommandozeilenwerkzeug installieren:** das Paket `dms-cli` aus Ihrer OG-Doc-Auslieferung. Prüfen
   Sie es mit `dms --help`.
2. **Gateway-Adresse setzen und anmelden:** `dms login --gateway-url https://ogdoc.ihre-behoerde.de`.
   Benutzername und Passwort geben Sie selbst ein, nie im Chat mit dem Agenten.
3. **Den Assistenten einbinden:** Entpacken Sie `og-doc-admin-skill-<version>.zip` in den Skill-Ordner
   Ihres Agenten, für Claude Code z. B. `~/.claude/skills/`. Andere Agenten können `SKILL.md` direkt als
   Anleitung lesen.
4. **Ausprobieren:** „Läuft bei uns alles?" ist eine reine Leseanfrage und ein guter erster Test.

## Welche Rollen das Konto braucht

Der Assistent kann nur, was Ihre Rollen erlauben. Für den vollen Aufgabenkatalog:

| Rolle | Wofür |
|---|---|
| `domain-admin-users` | Benutzer, Rollenzuweisungen, Gruppen, Berechtigungen erklären |
| `domain-admin-config`, `domain-admin-config-read` | Konfigurationspakete, Dokument- und Ordnertypen, Prozesse, Vier-Augen-Einstellungen |
| `domain-admin-monitoring` | Alarmregeln, Wartungsfenster, Sensoren |
| `domain-admin-mail` | Postfächer |
| `domain-admin-retention` | Papierkorb-Frist, Löschgründe |
| `domain-admin-folder-config`, `domain-admin-document-config` | Ordner- und Exporteinstellungen |
| `domain-admin-workflow-instances` | laufende Prozesse überspringen, zurücksenden, migrieren, beenden |
| `domain-admin-license` | Lizenzstatus |
| `domain-admin-notification-read` | fehlgeschlagene Benachrichtigungen sehen |
| optional `domain-admin-query-console` | „Wer hat was geändert?" (Prüfpfad durchsuchen) |

**Nicht vergeben** für die Arbeit mit dem Assistenten: `domain-admin-emergency`,
`domain-admin-query-console-manipulate`, `domain-admin-pii-reveal`, `domain-admin-deletion-vs`.

## Was er kann

| Bereich | Beispiele |
|---|---|
| Benutzer und Rechte | anlegen, Rolle in einem Ordner geben, Gruppen pflegen, Abteilungen (Organisationseinheiten) einrichten |
| Konfigurationspakete | „Was würde das eGov-Paket ändern?", Paket oder Teile davon einspielen |
| Dokument- und Ordnertypen | neue Typen, Felder, Aufbewahrungsfristen, Ablageregeln |
| Überwachung | Alarmregeln mit Vorschau, Wartungsfenster, aktive Alarme, Log-Level je Dienst oder Bereich (DEBUG läuft automatisch ab) |
| Vier-Augen | offene Anträge anzeigen und erklären; Vier-Augen für eine Aktion ein- oder ausschalten |
| Poststelle | Postfächer einrichten und testen |
| Aufbewahrung | Papierkorb-Frist, Löschgründe, Erinnerungen |
| Prozesse | neue Prozessversion einspielen; eine Instanz überspringen, zurücksenden, migrieren, beenden |
| Erscheinungsbild | Produktname, Akzentfarbe, Exportstempel |
| Diagnose | „Warum sieht X den Ordner Y nicht?", „Was ist fehlgeschlagen?", „Welche Alarme sind aktiv?", Lizenz, Dienste |

Was er **nicht** kann, steht unter „Was er nie tut". Für Bereiche ohne Kommando (z. B. Teamräume,
Signaturkonfiguration, Kennzeichen-Formate außerhalb von Typen) verweist er auf die Admin-Oberfläche.

## Wie Sie seine Änderungen nachvollziehen

- **Im Prüfpfad** (Admin-Oberfläche „Forensik-Trace", oder `dms query events list --actor <Ihre-ID>`)
  erscheinen die Änderungen unter Ihrem Namen, wie jede andere Admin-Aktion.
- **Begründungen:** Eingriffe in laufende Prozesse tragen die Begründung, die der Assistent Ihnen vorher
  gezeigt hat.
- **Vier-Augen-Anträge** zeigen Sie als Antragsteller.
- **Seine Zusammenfassung** am Ende jeder Änderung (vorher → nachher, angelegte Objekte, Antrags-IDs)
  können Sie für Ihre Dokumentation übernehmen.

## Grenzen

- **Er arbeitet mit dem, was OG Doc meldet.** Eine Vorschau zeigt den aktuellen Stand. Was ein anderer
  Administrator zwischen Vorschau und Ausführung ändert, erkennt er erst beim Prüfen.
- **Er ist so gut wie seine Anleitung.** Neue Funktionen von OG Doc kennt er erst mit einer neuen Version
  des Assistenten. Diese liegt jeder OG-Doc-Auslieferung bei.
- **Er ersetzt keine fachliche Entscheidung.** Ob eine Abteilung eine Rolle bekommen soll, entscheiden
  Sie; er sorgt dafür, dass es richtig und nachvollziehbar umgesetzt wird.
