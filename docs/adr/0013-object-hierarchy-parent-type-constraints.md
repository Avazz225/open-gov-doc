# 0013 — Erzwungene Objekt-Hierarchie: object_type_id/Root-Flag statt aufgelöstem Namen über die Service-Grenze

**Status:** akzeptiert
**Kontext:** Konzept 2.2a, Session P5b-S1

## Entscheidung

Die erzwungene Objekt-Hierarchie ("Ordner-/Dokumentklasse X darf nur unter Elternklasse Y liegen", 2.2a) wird wie folgt umgesetzt:

- `allowed_parent_types: list[str] | None` (Object-Type Service) listet Namen zulässiger Eltern-Ordnerklassen, mit dem Sentinel `"$ROOT"` für "direkt unter der Wurzel". Leer/`None` = überall platzierbar (Rückwärtskompatibilität zu allen bisherigen Objekttypen).
- Die eigentliche Prüfung bleibt in `dms-constraint-engine` (neue Funktion, in `validate()` integriert) - **kein neuer Service**, konsistent mit ADR 0003 (Constraint Engine als geteilte Bibliothek statt eigenständiger Service).
- **Aufrufer (Folder/Document Service) übertragen `parent_object_type_id: int | None` + `parent_is_root: bool` an `POST /object-types/{id}/validate`, nicht den bereits aufgelösten Namen der Elternklasse.** Object-Type Service selbst löst `parent_object_type_id` zu einem Namen auf (eigene DB-Abfrage).

## Begründung

- **Object-Type Service bleibt die einzige Quelle der Wahrheit für Objekttyp-Namen.** Folder Service und Document Service kennen von ihrem jeweiligen Elternordner ohnehin nur dessen `object_type_id` (opake Referenz, wie bei allen anderen Cross-Service-Beziehungen in diesem System, 3.1) - sie müssten sonst selbst einen zusätzlichen `GET /object-types/{id}`-Aufruf einschieben, nur um den Namen aufzulösen, bevor sie ihn wieder zurück an genau den Service schicken, der ihn kennt. Ein Roundtrip weniger pro Platzierungs-Prüfung.
- **`"$ROOT"` ist bewusst ein separates Flag (`parent_is_root`), keine Ableitung aus „Elternordner hat keinen Objekttyp".** Die Wurzel selbst hat `object_type_id = None` (sie kann nie eine eigene Klasse tragen, siehe `folder-service`), aber das ist nicht dasselbe wie ein gewöhnlicher, schlicht untypisierter Zwischenordner - ohne die explizite Unterscheidung könnte eine Klasse mit `allowedParentTypes: ["$ROOT"]` fälschlich auch unter jedem beliebigen untypisierten Ordner platziert werden. Ein Elternordner ohne eigenen Objekttyp erfüllt daher **keine** `allowedParentTypes`-Vorgabe, die einen konkreten Namen oder `"$ROOT"` verlangt - das ist eine bewusste, in `dms-constraint-engine`s Tests festgehaltene Entscheidung.
- **Nur Ordnerklassen (`applies_to == "folder"`) dürfen referenziert werden.** Nur Ordner können Elternobjekte sein (2.1) - ein `allowedParentTypes`-Eintrag, der auf eine Dokumentklasse verweist, wäre unmöglich zu erfüllen und wird deshalb schon bei der Objekttyp-Anlage/-Änderung mit `422` abgelehnt (Object-Type Service, `repository._validate_allowed_parent_types`), nicht erst beim ersten tatsächlichen Platzierungsversuch.
- **`icon` ist nur für Ordnerklassen zulässig** (ebenfalls `422` bei Verstoß) - Dokumentklassen haben laut Konzept 2.2a keine Icon-Anzeige vorgesehen (die käme im Explorer ohnehin nur für Ordner in Frage).
- **Durchsetzung bei Anlage *und* Verschieben von Ordnern, nur bei Anlage von Dokumenten** - Dokumente haben keine Verschiebe-Operation (bewusst unveränderlicher `folder_id`, siehe `docs/services/document-service.md`), Ordner dagegen schon (`PATCH /folders/{id}` mit `parent_id`, seit P3-S3 vorhanden, aber bislang nie gegen Objekttyp-Constraints re-validiert).
- **Keine rückwirkende Prüfung bestehender Ablagen**, wenn eine Klassen-Definition nachträglich verschärft wird (z. B. `allowedParentTypes` wird nach dem Anlegen vieler Instanzen ergänzt) - konsistent mit dem generellen Prinzip dieses Systems, Constraints nur bei Schreiboperationen zu prüfen, nicht per Hintergrundjob gegen den gesamten Bestand (vergleichbar mit der Objekttyp-Attributvalidierung selbst, die bei einer nachträglichen Verschärfung ebenfalls nur künftige Schreibvorgänge betrifft). Als offener Punkt in Konzept 13 festgehalten.
- **Keine Zyklen-Erkennung über mehrere Klassen hinweg** (z. B. A erlaubt nur B als Elternklasse, B erlaubt nur A) - eine vollständige Erreichbarkeitsprüfung bis zur Wurzel wäre für den aktuellen Umfang Overengineering; eine fehlerhafte, nie erfüllbare Konfiguration fällt spätestens beim ersten gescheiterten Platzierungsversuch auf (kein stiller Fehlerzustand, nur kein Anlage-Zeit-Check).

## Konsequenzen

- Object-Type Service, Folder Service und Document Service ändern sich; Permission Service, Storage Service, Search Service etc. sind unberührt (keine dieser Services kennt Objekttypen).
- `ObjectTypeClient.validate()` (identischer Code in `folder-service` und `document-service`, wie schon vor dieser Session) bekommt zwei neue optionale Parameter (`parent_object_type_id`, `parent_is_root`) - Standardwerte halten die Signatur für Aufrufer ohne Platzierungs-Kontext rückwärtskompatibel.
- `FolderClient` in `document-service` wurde von `exists(folder_id) -> bool` auf `get(folder_id) -> dict | None` erweitert (liefert jetzt den vollen Ordner-Body inkl. `object_type_id`, statt nur einen Boolean zu verwerfen) - der einzige bisherige Aufrufer (`create_document`) wurde entsprechend angepasst.
- Admin-seitige GUI zum Setzen von `allowedParentTypes`/`icon` folgt erst mit P5b-S3 (GUI-Objekttyp-/Layout-Designer) - diese Session deckt ausschließlich Backend-Datenmodell und -Durchsetzung ab, verifiziert direkt über die API (curl/pytest), nicht über die Admin-UI.
