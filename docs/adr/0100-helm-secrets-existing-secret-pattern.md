# 0100 — Helm: Postgres-/Keycloak-/MinIO-Passwörter über `existingSecret`-Muster statt Klartext-Env

**Status:** akzeptiert (P26-S3, siehe `IMPLEMENTATION_PLAN.md`)
**Kontext:** Konzept-übergreifend (Deployment/Betrieb), betrifft `infra/k8s/dms/` (Phase 26, Fortsetzung von [ADR 0099](0099-helm-single-chart-values-driven-service-map.md))

## Entscheidung

Die drei zustandsbehafteten Infrastruktur-Komponenten mit einem Admin-Passwort
(Postgres, Keycloak, MinIO — NATS/Redis laufen in diesem Chart ohne Auth,
siehe ADR 0099) bekommen je ein `<component>.existingSecret`-Feld in
`values.yaml`, nach dem in der Helm-Welt etablierten Muster
"existingSecret-if-set-else-generate" (siehe z. B. Bitnami-Charts):

- **Leer (Default)**: `templates/secrets.yaml` generiert ein Kubernetes-
  `Secret` aus dem Klartext-`*.auth.password`/`*.admin.password`-Wert, der
  bereits seit P26-S1 in `values.yaml` steht (Dev-/Test-Komfort, Parität zum
  bestehenden Compose-Dev-Setup) — ausdrücklich NICHT für Produktion gedacht,
  weiterhin so dokumentiert wie schon in ADR 0099.
- **Gesetzt**: das referenzierte, bereits vorhandene Secret wird stattdessen
  verwendet, `templates/secrets.yaml` generiert dafür NICHTS (vermeidet den
  klassischen Helm-Anti-Pattern-Fall eines ungenutzten, verwaisten
  generierten Secrets neben dem echten). Produktionspfad: entweder
  `--set postgresql.existingSecret=...` zusammen mit einer separaten,
  NICHT im Repo liegenden values-Datei mit echten Passwörtern, oder ein vorab
  per External-Secrets-Operator/Sealed-Secrets/manuell angelegtes Secret.
- Für den `external`-Zweig (Postgres/MinIO bei `enabled: false`) MUSS
  `<component>.external.existingSecret` gesetzt sein — dieses Chart kennt das
  Passwort einer bereits vorhandenen externen Instanz naturgemäß nicht und
  generiert dafür nichts.
- Jede der drei Deployments (bundled Postgres/Keycloak/MinIO selbst) sowie
  jeder der 32 zustandslosen Services (`DMS_POSTGRES_PASSWORD`) konsumieren
  das Passwort ausschließlich über `valueFrom.secretKeyRef`, nie mehr als
  literalen `value:`-Env-Eintrag.
- `DMS_POSTGRES_DSN` (ein zusammengesetzter Verbindungsstring, kein einzelnes
  Feld) referenziert das Passwort NICHT direkt über `secretKeyRef` (technisch
  nicht möglich — ein `secretKeyRef` liefert immer den kompletten Wert eines
  einzelnen Env-Eintrags, keinen Teilstring), sondern über die native
  Kubernetes-Env-Var-Referenzsubstitution: `DMS_POSTGRES_PASSWORD` wird zuerst
  per `secretKeyRef` gesetzt, `DMS_POSTGRES_DSN` baut sie danach im selben
  Container-`env`-Array per `$(DMS_POSTGRES_PASSWORD)` ein — Kubernetes löst
  `$(VAR_NAME)` zur Podstart-Zeit auf, wenn die referenzierte Variable vorher
  im selben `env`-Array steht (offizielles, dokumentiertes k8s-Muster für
  genau diesen Fall). Ersetzt den `${DMS_POSTGRES_PASSWORD}`-Platzhalter aus
  P26-S1 (geschweifte Klammern — von Helm nie aufgelöst UND von Kubernetes
  nicht als Referenz erkannt, reine Doku-Attrappe ohne Funktion) durch einen
  tatsächlich funktionierenden Mechanismus. `KC_DB_PASSWORD` im bundled
  Keycloak-Deployment nutzt denselben Postgres-Secret-Verweis wie
  `DMS_POSTGRES_PASSWORD` (Keycloak nutzt dieselbe Postgres-Instanz, siehe
  `infra/docker-compose.yml`).

## Begründung

- **`existingSecret`-Muster statt z. B. `sealed-secrets`/`external-secrets`
  als Chart-Abhängigkeit**: dieses Chart soll in möglichst vielen
  Cluster-Umgebungen ohne zusätzliche Operator-Voraussetzung installierbar
  bleiben (siehe ADR 0099 "kein echtes Cluster-Deployment in Phase 26
  gefordert" — die Zielumgebung ist zum jetzigen Zeitpunkt unbekannt). Das
  `existingSecret`-Feld ist Operator-agnostisch: ein Betreiber kann das
  referenzierte Secret mit JEDEM Mechanismus seiner Wahl vorab anlegen (auch
  einem einfachen `kubectl create secret` für kleine Installationen), ohne
  dass dieses Chart eine bestimmte Lösung voraussetzt oder selbst mitbringt.
- **Warum trotzdem ein generierter Klartext-Default statt `existingSecret`
  verpflichtend zu machen**: ein sofort lauffähiges `helm install` ohne
  vorherigen manuellen Secret-Anlage-Schritt ist für die Dev-/Lern-/Demo-
  Nutzung dieses Charts (analog zum bestehenden `docker-compose.yml`-Dev-
  Setup) weiterhin wichtig — genau der bereits in ADR 0099 dokumentierte
  Kompromiss, hier nur um einen echten Secret-Mechanismus ergänzt statt
  eines rohen Klartext-Env-Werts.
- **`$(VAR_NAME)`-Substitution statt eines vollständigen "dsn"-Secret-Keys**:
  eine Alternative wäre gewesen, den kompletten zusammengesetzten
  DSN-String (inkl. Passwort) selbst als Secret-Wert abzulegen. Verworfen,
  weil das für den `external`-Zweig bedeuten würde, dass das Chart den
  gesamten String (inkl. Host/Port/Database/Username) kennen müsste, obwohl
  diese Felder bereits einzeln in `values.yaml` (nicht geheim) stehen — nur
  das Passwort ist schützenswert. Die `$(VAR_NAME)`-Substitution trennt
  sauber: geheime Fragmente (Passwort) kommen aus dem Secret, alles andere
  bleibt eine gewöhnliche, aus `values.yaml` berechnete Helm-Template-
  Ausgabe.
- **Kein `required`-Guard auf `external.existingSecret`**: eine `required`-
  Direktive hätte `helm template`/`helm install` hart abbrechen lassen,
  sobald `enabled: false` gesetzt ist, ohne dass gleichzeitig
  `external.existingSecret` gesetzt wurde — das hätte den in dieser Session
  geforderten Toggle-Test (`--set postgresql.enabled=false --set
  postgresql.external.host=...` OHNE `existingSecret`) zum Scheitern
  gebracht. Bewusst zugunsten der Testbarkeit/Robustheit nicht erzwungen;
  ein leerer Secret-Name rendert zu einem `secretKeyRef` mit leerem `name:`,
  was bei einem echten `kubectl apply` sichtbar fehlschlagen würde (kein
  stiller Fehler) — für eine spätere Session, die reale Cluster-Deployments
  angeht, wäre ein `required` bzw. eine `NOTES.txt`-Warnung nachrüstbar.

## Konsequenzen

- `values.yaml` bleibt weiterhin der einzige Ort mit Klartext-Dev-Passwörtern
  im Repo — jetzt aber nur noch als Input für ein generiertes Secret, nicht
  mehr direkt als Pod-Env-Wert sichtbar (z. B. in `kubectl describe pod`-
  Ausgaben, die `value:`-Felder anzeigen, `valueFrom.secretKeyRef`-Felder
  dagegen nur den Secret-/Key-Namen, nicht den Wert selbst).
- Ein Wechsel von generiertem Secret auf `existingSecret` (oder umgekehrt)
  ändert den Secret-*Namen*, den ein Deployment referenziert — ein laufendes
  Deployment bemerkt das erst beim nächsten Pod-Neustart (kein automatisches
  Secret-Hot-Reload in Kubernetes, unabhängig von diesem Chart).
- `infra/k8s/dms/files/postgres-init/001-schemas.sql` (ConfigMap-Inhalt für den
  bundled Postgres, legt das `keycloak`-Schema an) ist eine manuell zu
  pflegende Kopie von `infra/postgres-init/001-schemas.sql` — Helms
  `.Files.Glob` kann nur innerhalb des Chart-Verzeichnisses lesen, ein
  Verweis auf den Compose-Pfad ist nicht möglich. Künftige Sessions, die
  `infra/postgres-init/` um weitere `CREATE SCHEMA`-Zeilen ergänzen, müssen
  die Kopie unter `infra/k8s/dms/files/postgres-init/` mitpflegen.
- `storageService.targets[].endpoint_url` (secondary-s3-Ziel) nutzt seither
  ebenfalls einen von `dms.storageServiceTargetsEnv` ersetzten Platzhalter
  (`__DMS_MINIO_ENDPOINT__` statt fest eingebranntem Compose-Hostnamen
  `minio`) — kein direkter Bestandteil dieser ADR-Entscheidung, aber vom
  selben Session-Kontext (reales `templates/minio.yaml` macht diesen bereits
  seit P26-S1 latent vorhandenen Bug erst sichtbar/relevant). Die
  `access_key`/`secret_key`-Felder desselben Ziels bleiben bewusst weiterhin
  Klartext-Dev-Werte — eine Secret-Anbindung dieser storage-service-
  spezifischen Ziel-Liste ist ein offener Punkt für eine spätere Session.
