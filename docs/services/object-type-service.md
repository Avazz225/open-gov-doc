# object-type-service

**Verantwortung:** Objekttyp-Definitionen (Attribute, Pflichtfelder, Namenskonventionen, bedingte Regeln) für Dokumente und Ordner (Konzept 2.2) sowie deren Validierung (4.5, "Constraint Engine"). Seit **P6-S7** trägt jede Dokumentklasse zusätzlich ein optionales Mindest-Signaturniveau (3.10), durchgesetzt vom `signature-service`, nicht hier.

**Konzept-Referenz:** 2.2, 4.5, 3.10
**Eigenes Postgres-Schema:** `object_type` (Tabelle `object_type`)

## API

| Methode | Pfad | Beschreibung |
|---|---|---|
| `POST` | `/object-types` | Anlegen (`name`, `applies_to`, `attributes`, `naming_constraints`, `conditions`, `allowed_parent_types`, `icon`, `required_signature_level`, `default_retention_days`, `deletion_reason_required_override`, `default_archive_after_days`, `archive_encryption_enabled`) — 409 bei doppeltem Namen, 422 bei ungültigem `allowed_parent_types`/`icon`/`required_signature_level`/`default_retention_days`/`default_archive_after_days` (siehe 2.2a bzw. "Mindest-Signaturniveau"/"Aufbewahrung"/"Aussonderung" unten) |
| `GET` | `/object-types?applies_to=document\|folder` | Liste, optional gefiltert |
| `GET` | `/object-types/{id}` | Einzelne Definition |
| `PUT` | `/object-types/{id}` | Definition ersetzen (Attribute/Naming/Conditions/`allowed_parent_types`/`icon`/`required_signature_level`/`default_retention_days`/`deletion_reason_required_override`/`default_archive_after_days`/`archive_encryption_enabled`) — `name`/`applies_to` bleiben unveränderlich |
| `DELETE` | `/object-types/{id}` | Löschen |
| `POST` | `/object-types/{id}/validate` | `{name, attributes, parent_object_type_id?, parent_is_root?}` → `{valid, errors}` — Platzierungs-Parameter seit P5b-S1 (2.2a) |
| `GET` | `/object-types/{id}/layouts/{purpose}` | Formular-Layout (`purpose`: `display`\|`search`\|`upload`, 2.2b, seit P5b-S2) — liefert ein gespeichertes Override (`is_custom: true`) oder ein aus den aktuellen Attributen generiertes Smart Layout (`is_custom: false`), 404 bei unbekannter `object_type_id` |
| `PUT` | `/object-types/{id}/layouts/{purpose}` | Speichert ein explizites Layout-Override — 422 bei Referenz auf ein unbekanntes Attribut, 404 bei unbekannter `object_type_id` |
| `DELETE` | `/object-types/{id}/layouts/{purpose}` | Entfernt ein Override (Reset auf das generierte Smart Layout) — idempotent, 404 nur bei unbekannter `object_type_id` |
| `POST` | `/object-types/{id}/next-kennzeichen` | Kennzeichengenerator (2.2, seit P5e-S1): atomarer Inkrement+Format-Aufruf, liefert `{kennzeichen: "2026-001"}` — 404 bei unbekannter `object_type_id` oder wenn kein `kennzeichen_format` konfiguriert ist |
| `GET` | `/kennzeichen-config` | Globaler Anzeige-Standard lesen (`show_before_filename`, seit P5e-S3) |
| `PUT` | `/kennzeichen-config` | Globalen Anzeige-Standard ändern — wirkt sofort für alle Dokumentenarten ohne eigenen `kennzeichen_display_override` |
| `GET` | `/healthz` | Health-Check |

## Datenmodell

`object_type`: `id`, `name` (unique), `applies_to` (`"document"`\|`"folder"`), `attributes` (JSON-Liste, siehe Schema unten), `naming_constraints` (JSON, nullable), `conditions` (JSON-Liste), `allowed_parent_types` (JSON-Liste von Objekttyp-Namen oder `"$ROOT"`, nullable — 2.2a, seit P5b-S1), `icon` (String, nullable, nur für `applies_to="folder"` — 2.2a, seit P5b-S1), `kennzeichen_format` (String, nullable, nur für `applies_to="document"` — 2.2, seit P5e-S1), `kennzeichen_display_override` (Boolean, nullable, Tri-State, nur für `applies_to="document"` — 2.2, seit P5e-S1), `required_signature_level` (String `"ses"`\|`"aes"`\|`"qes"`, nullable, nur für `applies_to="document"` — 3.10, seit P6-S7), `default_retention_days` (Integer, nullable, `>= 0`, für **beide** `applies_to`-Werte — 5.2, seit P7-S1), `deletion_reason_required_override` (Boolean, nullable, Tri-State wie `kennzeichen_display_override`, für beide `applies_to`-Werte — 5.2a, seit P7-S1), `default_archive_after_days` (Integer, nullable, `>= 0`, für beide `applies_to`-Werte — 5.6, seit P7-S3), `archive_encryption_enabled` (Boolean, Default `false`, für beide `applies_to`-Werte — 5.6, seit P7-S3), `created_at`/`updated_at`.

`object_type_layout` (2.2b, seit P5b-S2): `object_type_id` + `purpose` (`"display"`\|`"search"`\|`"upload"`) als zusammengesetzter Primärschlüssel (Fremdschlüssel auf `object_type.id`, `ON DELETE CASCADE`), `layout` (JSON: `{rows: [{columns: [{attribute, label, required}]}], responsive_breakpoint_px}`), `created_at`/`updated_at`. Nur explizite Abweichungen vom generierten Smart Layout werden hier gespeichert (siehe ADR 0014) — fehlt eine Zeile, gilt automatisch der aktuelle generierte Stand.

`object_type_sequence` (2.2, seit P5e-S1): `object_type_id` + `jahr` als zusammengesetzter Primärschlüssel (Fremdschlüssel auf `object_type.id`, `ON DELETE CASCADE`), `naechste_nummer` (Integer). Eine Zeile je Objekttyp und Jahr — siehe "Kennzeichengenerator" unten.

`kennzeichen_config` (2.2, seit P5e-S3): einzelne Zeile (`id=1`, gleiches Muster wie `OcrConfig`/`UploadConfig`) — `show_before_filename` (Boolean, Default `true`), `updated_at`. Kein FK-Bezug zu `object_type`, daher separat von der `object_type`-CASCADE-Truncate in den Tests zu leeren.

## Objekttyp-Schema (2.2)

```json
{
  "attributes": [
    { "name": "Rechnungsnummer", "type": "string", "required": true, "pattern": "RE-\\d{6}" },
    { "name": "Betrag", "type": "decimal", "required": true, "min": 0 }
  ],
  "naming_constraints": {
    "mustContain": ["Rechnungsnummer"],
    "pattern": "{Rechnungsnummer}_{Datum}"
  },
  "conditions": [
    { "if": "Betrag > 10000", "then": "require:Kostenstelle" }
  ]
}
```

Unterstützte Attribut-Typen: `string`, `decimal`, `integer`, `boolean`, `date`, `reference`.

## Erzwungene Objekt-Hierarchie & Icons (2.2a, seit P5b-S1)

Jeder Objekttyp kann über `allowed_parent_types` festlegen, unter welchen Ordnerklassen er platziert werden darf — z. B. `meinTopLevelOrd` nur unter `"$ROOT"` (direkt unter der Wurzel), `meinSecondLevelOrd` nur unter `meinTopLevelOrd`, `meinDoc` nur unter `meinSecondLevelOrd`. Mehrfachangabe (mehrere zulässige Elternklassen) ist erlaubt. Fehlt das Feld oder ist die Liste leer, bleibt der Typ wie bisher überall platzierbar.

- **Validierung bei Anlage/Änderung des Objekttyps** (nicht erst beim Platzierungsversuch): jeder Eintrag muss entweder `"$ROOT"` sein oder eine bereits existierende Ordnerklasse (`applies_to="folder"`) referenzieren — sonst `422`. Nur Ordner können Elternobjekte sein (2.1).
- **`icon`** ist nur für Ordnerklassen zulässig (`422` bei Dokumentklassen) — Anzeige im User-UI-Explorer vor dem Namen folgt erst mit P5b-S4.
- **Durchsetzung**: `POST /object-types/{id}/validate` nimmt `parent_object_type_id`/`parent_is_root` entgegen und löst daraus den Namen der Elternklasse selbst auf (dieser Service bleibt die einzige Quelle der Objekttyp-Namen, siehe **ADR 0013**) — Folder Service und Document Service übertragen nur, was sie ohnehin kennen (die `object_type_id` des Elternordners bzw. dass er die Wurzel ist), kein zusätzlicher Roundtrip nötig.
- **Kein Rückwirkungs-Check**: Verschärft man `allowed_parent_types` nachträglich, werden bereits bestehende Platzierungen nicht rückwirkend geprüft — nur künftige Anlagen/Verschiebungen (Konzept 13, offener Punkt).
- **Keine Zyklen-Erkennung** über mehrere Klassen hinweg (siehe ADR 0013, Konsequenzen).

## Kennzeichengenerator (2.2, seit P5e-S1)

Jede Dokumentklasse (`applies_to="document"`) kann ein Format-String-Feld `kennzeichen_format` erhalten, das bei jedem `POST .../next-kennzeichen`-Aufruf zu einem fertigen Aktenzeichen gerendert wird (z. B. `{YYYY}-{Laufende_Nummer}` → `2026-001`). `null` bedeutet: kein Generator konfiguriert.

- **Unterstützte Platzhalter**: `{YYYY}` (vierstelliges Jahr), `{YY}` (zweistellig), `{MM}`/`{DD}` (zweistellig), `{Laufende_Nummer}` (dreistellig nullgepolstert, z. B. `001`, wächst darüber hinaus einfach in der Ziffernzahl). `kennzeichen_format` muss `{Laufende_Nummer}` enthalten und darf keine anderen Platzhalter referenzieren — sonst `422` bei Anlage/Änderung des Objekttyps (analog zur `allowedParentTypes`-Referenzprüfung, 2.2a).
- **`kennzeichen_format`/`kennzeichen_display_override`** sind nur für `applies_to="document"` zulässig — `422` bei Ordnerklassen. `kennzeichen_display_override` ist ein Tri-State (`null`/`true`/`false`), der bei gesetztem Wert den globalen "Kennzeichen vor Dateinamen anzeigen"-Standard überschreibt.
- **Zähler-Reset pro Jahr**: die laufende Nummer setzt sich am 1. Januar automatisch zurück, je Objekttyp unabhängig (`object_type_sequence`, Primärschlüssel `{object_type_id, jahr}`). Nutzerentscheidung aus der Phase-5e-Planung (siehe `PROGRESS.md`), nicht pro Tag oder global fortlaufend.
- **Atomare, nebenläufigkeitssichere Vergabe**: `POST /object-types/{id}/next-kennzeichen` führt `INSERT ... ON CONFLICT DO NOTHING` (legt die Zähler-Zeile bei Bedarf an, ohne dass zwei gleichzeitige Erstaufrufe an einem Unique-Constraint scheitern) gefolgt von `SELECT ... FOR UPDATE` (sperrt die Zeile für die Dauer der Transaktion) aus — parallele Aufrufe werden dadurch serialisiert statt sich gegenseitig zu überschreiben. Live gegen den echten Stack sowie per `asyncio.gather`-Test mit fünf gleichzeitigen Aufrufen verifiziert (liefert garantiert `001`–`005`, keine Duplikate/Lücken).
- **Wer die Vergabe tatsächlich auslöst und wo `Kennzeichen` landet** (reservierter Attributschlüssel, `403` bei nachträglicher Änderung ohne `dms-admin`-Rolle) ist Aufgabe des Document Service (**P5e-S2**, siehe `docs/services/document-service.md`) — dieser Service liefert nur den fertig gerenderten String auf Anfrage, ohne selbst zu wissen, dass/wo er verwendet wird.
- **Globaler Anzeige-Standard** (`kennzeichen_config`, seit **P5e-S3**): `GET`/`PUT /kennzeichen-config` verwalten einen einzigen Schalter `show_before_filename` (Default `true`) — der von den Frontends aufgelöste effektive Wert je Dokumentenart ist `kennzeichen_display_override` (falls nicht `null`), sonst dieser globale Standard. Die Auflösung selbst passiert **client-seitig** in Admin-UI/User-UI, nicht hier — dieser Service liefert nur die beiden Rohwerte, kein eigener "resolved"-Endpunkt (kein Konsument bräuchte ihn synchron genug, um den zusätzlichen Roundtrip zu rechtfertigen).

## Mindest-Signaturniveau (3.10, seit P6-S7)

`required_signature_level` (`null`/`"ses"`/`"aes"`/`"qes"`) ist wie `kennzeichen_format`/`kennzeichen_display_override` nur für Dokumentklassen zulässig (`422` bei Ordnerklassen, gleiche Validierungsfunktion wie die übrigen dokumentklassen-exklusiven Felder). Dieser Service **setzt das Niveau nicht selbst durch** — er liefert es nur auf Anfrage; die eigentliche Durchsetzung passiert bei jedem Signiervorgang im `signature-service` (`GET /object-types/{id}` dort abgefragt, `400` bei zu niedrigem angefordertem Niveau), siehe `docs/services/signature-service.md`.

## Formular-Layouts (2.2b, seit P5b-S2)

Jeder Objekttyp trägt zusätzlich zu seinen Attributen ein Formular-Layout je Verwendungszweck (`display`/`search`/`upload`) — ein Zeilen/Spalten-Grid, das steuert, wie die Attribute in der User-UI (Metadaten-Anzeige, Suchmaske, Upload-Dialog, alle erst ab P5b-S4 tatsächlich angebunden) angeordnet werden.

- **Smart-Layout-Generierung** (`object_type_service.layout.generate_smart_layout`): packt die Attribute eines Objekttyps in Anlage-Reihenfolge zu je zwei Feldern pro Zeile, übernimmt den technischen Attributnamen zunächst 1:1 als `label` und spiegelt das `required`-Flag des Attributs zum Generierungszeitpunkt. Dieselbe Heuristik gilt für alle drei Verwendungszwecke.
- **Generiert, nicht persistiert**: Ohne gespeichertes Override liefert `GET .../layouts/{purpose}` bei jedem Aufruf ein frisch aus der aktuellen Attributliste berechnetes Layout (`is_custom: false`) — bleibt dadurch automatisch aktuell, auch wenn sich Attribute später ändern. Erst ein `PUT` friert einen Stand explizit ein (`is_custom: true`, Snapshot statt live Referenz). **Ausführliche Begründung: ADR 0014.**
- **Referenzprüfung beim Speichern**: `PUT` lehnt ein Layout ab (`422`), das ein nicht zum Objekttyp gehörendes Attribut referenziert — analog zur `allowedParentTypes`-Referenzprüfung (2.2a).
- **`DELETE` setzt gezielt einen einzelnen Verwendungszweck zurück** auf das generierte Smart Layout — idempotent, kein Fehler, falls nie ein Override existierte.
- **Kein GUI-Editor in dieser Session** — die geführte Attributauswahl/Anzeigename-Vergabe/Layout-Nachjustierung im Admin-UI folgt mit **P5b-S3**; diese Session deckt ausschließlich das Backend-Datenmodell, die Generierung und die Lese-/Schreib-/Reset-API ab, verifiziert per pytest/curl.
- **Kein Rückwirkungs-Check**: Ändert sich die Attributliste eines Objekttyps, nachdem bereits ein individuelles Layout gespeichert wurde, wird dieses nicht automatisch nachgeführt (kann danach ein entferntes Attribut referenzieren) — dieselbe bewusste Einschränkung wie bei `allowedParentTypes` (ADR 0013).

## Aufbewahrung & Löschgrund-Pflicht (5.2/5.2a, seit P7-S1)

`default_retention_days` und `deletion_reason_required_override` sind — anders als `kennzeichen_format`/`required_signature_level` — **nicht** auf `applies_to="document"` beschränkt, sondern für Dokument- **und** Ordnerklassen gleichermaßen zulässig, da `document-service` (P7-S1, Dokumente) und der geplante Nachfolger P7-S1b (Ordner) dasselbe Objekttyp-Schema für ihren jeweiligen Aufbewahrungs-Default nutzen. Einzige Validierung: `default_retention_days` muss `>= 0` sein (`422` sonst) — keine Referenzprüfung wie bei `allowedParentTypes`.

Beide Felder werden von `document-service` gelesen (`GET /object-types/{id}`), nicht von diesem Service ausgewertet — analog zu `required_signature_level`, das ebenfalls nur hier gespeichert, aber vom `signature-service` durchgesetzt wird. `default_retention_days` bestimmt beim Anlegen eines Dokuments ohne manuell gesetztes `retention_until` ein einmaliges `created_at + default_retention_days`-Datum (kein wiederholtes Nachschlagen bei späterer Typ-Änderung); `deletion_reason_required_override` überschreibt — falls nicht `null` — den globalen `RetentionConfig.deletion_reason_required`-Standard aus `document-service`.

## Aussonderung & Langzeitarchivierung (5.6, seit P7-S3)

`default_archive_after_days`/`archive_encryption_enabled` folgen exakt demselben Muster wie `default_retention_days`/`deletion_reason_required_override` oben — für beide `applies_to`-Werte zulässig, keine Referenzprüfung, `default_archive_after_days` muss `>= 0` sein. Gelesen wird ausschließlich von `document-service` (Auflösung von `Document.archive_after` beim Anlegen, analog `retention_until`) und `archival-service` (`GET /object-types/{id}`, um zu entscheiden, ob die Archivkopie eines Dokuments verschlüsselt werden muss) — dieser Service selbst wertet beide Felder nicht aus. Anders als `deletion_reason_required_override` ist `archive_encryption_enabled` bewusst **kein** Tri-State (kein globaler Standard, den es überschreiben könnte) — reine Ja/Nein-Konfiguration pro Objekttyp.

## Constraint Engine (4.5)

Die eigentliche Validierungslogik liegt in `libs/dms-constraint-engine` (reine, zustandslose Bibliothek) — **siehe ADR 0003** für die Begründung, warum das keine eigene Service ist. Dieser Service ist der einzige Ort, der die Lib importiert; andere Services (Document Service, Folder Service) rufen ausschließlich `/object-types/{id}/validate` über HTTP auf.

Unterstützt (Minimum laut 4.5): Pflichtfelder, bedingte Pflichtfelder, Musterprüfung (Regex) für Werte und Namen, Wertebereiche (`min`/`max`), sowie seit P5b-S1 die Platzierungs-Hierarchie aus 2.2a (`allowedParentTypes`/`parent_type_name`, siehe oben).

**Bewusst vereinfacht**: `type: "reference"` prüft nur das Format (nicht-leerer String), nicht die tatsächliche Existenz des referenzierten Objekts beim zuständigen Service — eine generische "Referenztyp → Service"-Auflösung existiert noch nicht.

**Nicht geprüft**: Ob ein aufrufender Service (Document/Folder Service) tatsächlich einen zu seinem eigenen Typ passenden `applies_to`-Wert verwendet (z. B. ein Dokument mit einem `"folder"`-Objekttyp) — liegt in der Verantwortung des Aufrufers.

## Events

Keine — reiner Referenzdaten-Dienst, wird synchron über HTTP abgefragt, nicht über Events konsumiert/publiziert.

## Selbst-Registrierung (Konzept 3.2a, seit P4-S1)

Registriert sich beim Start selbst bei der Registry (`libs/dms-registry-client`: Register, periodischer Heartbeat, Deregister beim Shutdown) - Grundlage für das Routing des API-Gateways (`docs/services/gateway-service.md`). Opt-in über `DMS_REGISTRY_SERVICE_BASE_URL`/`DMS_SELF_ADDRESS`; ohne beide Werte läuft der Service unverändert ohne Discovery.

## Sensoren (Konzept 10.1)

Noch keine — folgt in Phase 11.

## Tests

- `uv run pytest services/object-type-service/tests`: Repository (CRUD, `allowed_parent_types`/`icon`-Validierung inkl. Ablehnung unbekannter/Nicht-Ordner-Referenzen, Layout-Upsert/-Reset/-Attributreferenzprüfung, Kennzeichengenerator-Format-Validierung, Jahres-Zähler inkl. Unabhängigkeit je Objekttyp sowie ein `asyncio.gather`-Nebenläufigkeitstest mit fünf parallelen Aufrufen, globale Kennzeichen-Konfiguration inkl. Default), Smart-Layout-Generierung (`test_layout.py`, reine Funktionslogik), API (`/validate` inkl. `parent_is_root`/`parent_object_type_id`-Auflösung, 422 bei ungültigen 2.2a-Feldern, `/layouts/{purpose}` inkl. generiertem vs. gespeichertem Layout, 422/404-Fälle, `/next-kennzeichen` inkl. 404 ohne Format, `/kennzeichen-config` GET/PUT). Seit **P6-S7** zusätzlich: `required_signature_level` auf Ordnerklassen abgelehnt (422), auf Dokumentklassen persistiert (Repository + API). Seit **P7-S1** zusätzlich: `default_retention_days`/`deletion_reason_required_override` persistiert für Dokument- **und** Ordnerklassen, `default_retention_days < 0` abgelehnt (422). Seit **P7-S3** zusätzlich: `default_archive_after_days`/`archive_encryption_enabled` persistiert für Dokument- **und** Ordnerklassen, `default_archive_after_days < 0` abgelehnt (422). **81 Tests, alle grün** (vorher 76).
- **Live-Smoke-Test** (P5e-S1): `docker compose build object-type-service` + `up -d`, Objekttyp mit `kennzeichen_format` angelegt, zwei `POST .../next-kennzeichen`-Aufrufe lieferten `2026-001`/`2026-002`, dritter Objekttyp ohne Format lieferte `404` — Testdaten anschließend wieder gelöscht.

## Offene Punkte

- Referenz-Existenzprüfung (s. o.) nicht implementiert.
- Statusübergänge (4.5 nennt "bei Erstellung, Änderung und Statusübergängen") werden noch nicht ausgewertet — es gibt noch keinen Workflow-/Status-Mechanismus (folgt Phase 6).
- **Kein GUI-Editor für `allowed_parent_types`/`icon`/Formular-Layouts/Kennzeichengenerator** — folgt mit P5b-S3 (allowed_parent_types/icon) bzw. **P5e-S3** (Kennzeichengenerator); diese und die vorherige Session decken nur das Backend-Schema/die Durchsetzung/Generierung ab, verifiziert per curl/pytest.
- Kein Rückwirkungs-Check und keine Zyklen-Erkennung für `allowed_parent_types` (siehe ADR 0013); kein Rückwirkungs-Check für gespeicherte Layout-Overrides bei späteren Attributänderungen (siehe ADR 0014).
- User-UI-Konsum der Layouts (Metadaten-Panel/Suchmaske/Upload-Dialog auf layoutgesteuertes Rendering umstellen) folgt erst mit P5b-S4.
- Kein Rückwirkungs-Check, falls `kennzeichen_format` nachträglich geändert wird — bereits vergebene Kennzeichen behalten ihr altes Format.
- **Kein serverseitiger "resolved display"-Endpunkt** für `kennzeichen_config`/`kennzeichen_display_override` — jedes Frontend löst Override-vs-Standard selbst auf (siehe "Kennzeichengenerator" oben). Bei einem dritten Konsumenten wäre das ggf. zu zentralisieren.
