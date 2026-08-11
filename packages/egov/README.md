# eGov-Konfigurationspaket

Erstes, vom Projekt selbst gepflegtes Konfigurationspaket (Konzept §14.2) — eine sinnvolle
Standardkonfiguration für die deutsche öffentliche Verwaltung, sodass eine frische Installation
nach Anwendung direkt als einsatzbereites eGov-DMS nutzbar ist, statt jede der folgenden
Einstellungen erst einzeln manuell nachzubilden. Technisch ist ein Paket nichts anderes als ein
gewöhnliches Konfigurationsdokument (§7.3) mit einem zusätzlichen, rein beschreibenden `manifest`
(§14.1) — siehe [ADR 0058](../../docs/adr/0058-konfigurationspakete-manifest-realm-roles-and-gateway-import-route-split.md)
für das Format und [ADR 0059](../../docs/adr/0059-egov-paket-aktenplan-hierarchie-und-mehrstufige-vs-einstufung.md)
für die Inhalte dieses konkreten Pakets.

## Anwenden

- **Admin-UI** (empfohlen): `/config-packages/` → `config.json` auswählen → Vorschau → Anwenden.
- **CLI**: `dms config import packages/egov/config.json` (nutzt denselben, bereits bestehenden
  Konfigurationsimport wie jeder andere 7.3-Export).
- **Fleet-Management**: `POST /installations/{id}/provision` mit dem Inhalt dieser Datei als Body
  (zentrale Erstprovisionierung mehrerer Installationen, siehe `docs/services/fleet-management-service.md`).

Additiv/Upsert, wiederholt anwendbar (§14.1) — auch auf eine bereits laufende, teilweise anders
konfigurierte Installation anwendbar, kein Ersteinrichtungs-Zwang.

## Stand: Teil 1 (P17-S2)

Enthält bisher nur die Kategorie `object_types`:

| Objekttyp | Typ | Eltern | Attribute | Kennzeichen-Format | Aufbewahrung | VS-Einstufung |
|---|---|---|---|---|---|---|
| `Abteilung` | Ordner | `$ROOT` | — | — | — | — |
| `Aktenplan` | Ordner | `Abteilung` | — | — | — | — |
| `Akte` | Dokument | `Aktenplan` | Aktentitel*, Federführung* | `{Federführung}-{YYYY}-{Laufende_Nummer}` | 10 Jahre (3653 Tage) | — |
| `Verschlusssache-Akte` | Dokument | `Aktenplan` | Aktentitel*, Federführung* | `{Federführung}-{YYYY}-{Laufende_Nummer}` | 10 Jahre (3653 Tage) | VS-NfD |

\* Pflichtattribut. Die Aktenzeichen-Generierung selbst (`{Laufende_Nummer}`, jahresbasierter
Reset) ist der bereits bestehende Kennzeichengenerator (P5e-Sessions) — `{Federführung}` ist ein
seit P17-S2 neu unterstützter, **attributbasierter** Platzhalter (jeder Platzhalter, der kein
Datums-/Zähler-Platzhalter ist, wird als Attributname interpretiert), direkte Umsetzung des
Konzeptbeispiels `{Abteilung}-{YYYY}-{Laufende_Nummer}` — hier bewusst mit dem tatsächlich
sinnvolleren, bereits als Pflichtattribut vorgesehenen `Federführung` statt eines redundanten,
zweiten "Abteilung"-Attributs (die Akte liegt strukturell ohnehin schon unter einer
`Abteilung`-Ordnerinstanz).

Die Aufbewahrungsfrist (10 Jahre) und die Wahl, `Verschlusssache-Akte` mit `VS-NfD` statt einer
höheren Stufe vorzubelegen, sind **veränderbare Vorbelegungen, kein Systemzwang** — die konkrete
gesetzliche Frist/Einstufung bleibt je Bundesland/Rechtsgebiet in der Verantwortung der
Installation (Konzepttext, wörtlich).

## Ausstehend: Teil 2 (P17-S3)

Poststelle-Rollenvorlage (`realm_roles`-Kategorie), BPMN-Prozessvorlagen für gängige
Umlaufmappen-Muster (`workflows`-Kategorie), Vier-Augen-Vorbelegung für sensible Aktionstypen
(`approval_config`-Kategorie), Geschäftskalender mit bundeseinheitlichen/landesspezifischen
Feiertagen (`business_calendars`-Kategorie), erweiterte domänengetrennte Admin-Rollen
(`roles`-Kategorie) — ergänzt dieselbe `config.json`, keine neue Datei.
