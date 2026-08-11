# eGov-Konfigurationspaket

Erstes, vom Projekt selbst gepflegtes Konfigurationspaket (Konzept §14.2) — eine sinnvolle
Standardkonfiguration für die deutsche öffentliche Verwaltung, sodass eine frische Installation
nach Anwendung direkt als einsatzbereites eGov-DMS nutzbar ist, statt jede der folgenden
Einstellungen erst einzeln manuell nachzubilden. Technisch ist ein Paket nichts anderes als ein
gewöhnliches Konfigurationsdokument (§7.3) mit einem zusätzlichen, rein beschreibenden `manifest`
(§14.1) — siehe [ADR 0058](../../docs/adr/0058-konfigurationspakete-manifest-realm-roles-and-gateway-import-route-split.md)
für das Format, [ADR 0059](../../docs/adr/0059-egov-paket-aktenplan-hierarchie-und-mehrstufige-vs-einstufung.md)
für Teil 1 und [ADR 0060](../../docs/adr/0060-egov-paket-teil-2-vier-augen-luecken-und-umlaufmappen-prozessvorlagen.md)
für Teil 2 dieses konkreten Pakets.

## Anwenden

- **Admin-UI** (empfohlen): `/config-packages/` → `config.json` auswählen → Vorschau → Anwenden.
- **CLI**: `dms config import packages/egov/config.json` (nutzt denselben, bereits bestehenden
  Konfigurationsimport wie jeder andere 7.3-Export).
- **Fleet-Management**: `POST /installations/{id}/provision` mit dem Inhalt dieser Datei als Body
  (zentrale Erstprovisionierung mehrerer Installationen, siehe `docs/services/fleet-management-service.md`).

Additiv/Upsert, wiederholt anwendbar (§14.1) — auch auf eine bereits laufende, teilweise anders
konfigurierte Installation anwendbar, kein Ersteinrichtungs-Zwang.

## Aktenplan-Objekttyp-Hierarchie (Teil 1, P17-S2)

Enthält die Kategorie `object_types`:

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

## Poststelle, Prozessvorlagen, Vier-Augen, Geschäftskalender, Admin-Rollen (Teil 2, P17-S3)

Ergänzt dieselbe `config.json` (14.1: additiv/Upsert, keine neue Datei) um die restlichen fünf in
§14.2 genannten Bestandteile — `manifest.version` steht seit dieser Session auf `1.0.0`, der
ersten vollständigen Version des Pakets. Details/Begründung siehe
[ADR 0060](../../docs/adr/0060-egov-paket-teil-2-vier-augen-luecken-und-umlaufmappen-prozessvorlagen.md).

| Kategorie | Inhalt |
|---|---|
| `realm_roles` | `dms-poststelle` — Keycloak-Realmrolle für Posteingang/-ausgang (2.5), von `mail-connector` bereits durchgesetzt (seit P15-S3), hier erstmals paketiert. |
| `workflows` | Drei BPMN-Prozessvorlagen für Umlaufmappen-Muster (2.3/7.1): `egov_freigabe` (Entscheidung Genehmigt/Abgelehnt per `exclusiveGateway`), `egov_kenntnisnahme` und `egov_aufgabe` (je ein linearer `manualTask`). Quell-XML liegt zusätzlich unter [`workflows/`](workflows/) zur besseren Pflege/Diff-Lesbarkeit. |
| `approval_config` | Vier-Augen-Vorbelegung (4.3) für die drei in 14.2 genannten sensiblen Aktionstypen — `document.force_delete`, `folder.force_delete`, `document.force_unlock` (endgültige Löschung), `permission.role_assignment.create` (Berechtigungsänderung, seit P17-S3 real durchgesetzt), `config.import` (Konfigurationsimport, seit P17-S3 real durchgesetzt) — jeweils `requires_approval: true`. |
| `business_calendars` | `DE-Bund` (Default, neun bundeseinheitliche Feiertage) plus 16 Landeskalender `DE-BW` … `DE-TH` (jeweils vollständig inkl. Bundesfeiertage), reale Termine für 2026/2027. |
| `roles` | `Registratur/Aktenverwaltung` (`read`, `write`) und `Amtsleitung` (`read`, `write`, `scope_lock.bypass`) — erweiterte, domänengetrennte Admin-Rollen oberhalb der technischen `domain-admin-*`-Systemrollen (4.6). |

Die Vier-Augen-Vorbelegung setzt voraus, dass die jeweils genehmigende Person **nicht** mit der
initiierenden Person identisch ist (4.3) — nach Anwendung dieses Pakets erfordern endgültige
Löschung, Rollenzuweisung und Konfigurationsimport also grundsätzlich eine zweite Person, bevor sie
wirksam werden (`POST /approval-requests/{id}/approve`).
