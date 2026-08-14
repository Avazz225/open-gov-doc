# infra/k8s/

Helm-Chart für einen optionalen k8s/OCP-Betrieb des DMS (Phase 26, siehe
`IMPLEMENTATION_PLAN.md` und [ADR 0099](../../docs/adr/0099-helm-single-chart-values-driven-service-map.md)).
Lokale Entwicklung läuft weiterhin über `../docker-compose.yml` — dieses Chart
ist ein zusätzlicher, alternativer Deploy-Weg für ein echtes Kubernetes-/
OpenShift-Cluster, kein Ersatz für Compose in der Entwicklung.

## Struktur

```
infra/k8s/dms/
├── Chart.yaml
├── values.yaml          # zentrale Konfiguration — siehe Konventionen unten
├── .helmignore
├── files/
│   └── postgres-init/
│       └── 001-schemas.sql  # Kopie von infra/postgres-init/ (siehe dortiger Kommentar)
└── templates/
    ├── _helpers.tpl      # Namens-/Label-Helfer + generische Env-/Resource-Bausteine
    ├── deployment.yaml    # EIN Template für ALLE services:-Einträge (range)
    ├── service.yaml       # dito
    ├── hpa.yaml            # HorizontalPodAutoscaler, nur wo autoscaling.enabled
    ├── pdb.yaml             # PodDisruptionBudget, nur wo podDisruptionBudget.enabled
    ├── secrets.yaml         # Postgres-/Keycloak-/MinIO-Admin-Secrets (P26-S3, ADR 0100)
    ├── postgresql.yaml      # bundled Postgres: ConfigMap+PVC+Deployment+Service (P26-S3)
    ├── keycloak.yaml        # bundled Keycloak: Deployment+Service (P26-S3)
    ├── minio.yaml           # bundled MinIO: PVC+Deployment+Service (P26-S3)
    ├── nats.yaml            # bundled NATS (bundled-only): PVC+Deployment+Service (P26-S3)
    ├── redis.yaml           # bundled Redis (bundled-only): Deployment+Service (P26-S3)
    ├── storage-cronjob.yaml  # CronJob: externer Trigger für storage-service-Replikation (P26-S4, ADR 0101)
    └── NOTES.txt
```

Die fünf zustandsbehafteten Infrastruktur-Komponenten (`postgresql.yaml` /
`keycloak.yaml` / `minio.yaml` / `nats.yaml` / `redis.yaml`) haben bewusst
eigene Templates statt über den generischen `services:`-Mechanismus zu
laufen — Volumes, abweichende Image-Quellen/Ports/Healthchecks und
Secret-Verweise passen strukturell nicht in das für zustandslose
FastAPI-Services gebaute generische Schema (siehe ADR 0099/0100).

**Ein** Chart für alle Container aus `infra/docker-compose.yml`, kein
Chart/Template pro Service — siehe ADR 0099 für die Begründung. Ein neuer
Service bedeutet einen neuen Eintrag unter `services:` in `values.yaml`, kein
neues Template.

## Verifizieren

Kein echtes Cluster-Deployment in Phase 26 gefordert — es reicht:

```bash
helm lint infra/k8s/dms
helm template my-release infra/k8s/dms
```

`helm template ... | kubectl apply --dry-run=client -f -` ist ein optionaler
Zusatz-Check, falls `kubectl` verfügbar ist — nicht Voraussetzung.

Falls `helm` in der jeweiligen Umgebung nicht vorinstalliert ist: Download als
statisches Binary von <https://get.helm.sh> (kein root/`apt` nötig), z. B.:

```bash
curl -sSL -o helm.tar.gz https://get.helm.sh/helm-v3.15.4-linux-amd64.tar.gz
tar xzf helm.tar.gz
./linux-amd64/helm lint infra/k8s/dms
```

## `values.yaml`-Konventionen

- **`services.<name>`**: ein Eintrag je Container, identischer Shape für
  jeden Service (`enabled`, `image.{repository,tag}`, `port`, `replicas`,
  `resources`, `autoscaling`, `podDisruptionBudget`, `dependsOnServices`,
  `env`). Details/Feldbeschreibung siehe Kommentarblock am Anfang von
  `values.yaml`.
- **`resources.baseline`**: Default-Requests/-Limits für alle Services ohne
  eigenen `resources`-Block. Ein Service mit eigenem (auch nur teilweisem)
  `resources`-Block überschreibt selektiv (Deep-Merge in
  `templates/deployment.yaml`).
- **`postgresql` / `keycloak` / `minio`**: `enabled: true` = im Chart
  gebündelte Instanz (echte Deployments+Service(+PVC) seit P26-S3, siehe
  `templates/postgresql.yaml`/`keycloak.yaml`/`minio.yaml`), `enabled: false`
  + `external.*` = bereits vorhandene externe Instanz nutzen (bei Postgres/
  MinIO zusätzlich `external.existingSecret` — Pflichtfeld, siehe unten).
  `nats`/`redis` sind bewusst bundled-only ohne diesen Umschalter.
- **`<component>.existingSecret`** (P26-S3, ADR 0100): Standard-Helm-
  Konvention "existingSecret-if-set-else-generate" für Postgres-/Keycloak-/
  MinIO-Admin-Passwörter. Leer (Default) = `templates/secrets.yaml` generiert
  ein Secret aus dem Klartext-`*.auth.password`/`*.admin.password`-Wert in
  `values.yaml` (Dev-/Test-Komfort, NICHT für Produktion). Gesetzt = dieses
  bereits vorhandene Secret referenzieren, nichts wird generiert — die
  Deployments konsumieren das Passwort in beiden Fällen ausschließlich über
  `valueFrom.secretKeyRef`, nie als literalen Env-Wert. `DMS_POSTGRES_DSN`
  baut das Passwort über die native Kubernetes-`$(VAR_NAME)`-Substitution in
  den Verbindungsstring ein (siehe `templates/_helpers.tpl`
  `dms.postgresDsn`-Kommentar).
- **`storageService.targets`**: native YAML-Liste (Pendant zu
  `BackendTargetConfig` in `services/storage-service`), wird zur Laufzeit per
  `toJson` in die vom Service gelesene `DMS_TARGETS`-Env-Var serialisiert
  (nur für Services mit `storageTargetsEnv: true`, aktuell `storage-service`).
  Das `secondary-s3`-Ziel nutzt seit P26-S3 den Platzhalter
  `__DMS_MINIO_ENDPOINT__` statt eines fest eingebrannten Compose-Hostnamens
  (respektiert `minio.enabled` bundled/external, siehe
  `dms.storageServiceTargetsEnv`).
- **`services.<name>.usesRedis`** (P26-S3, analog `usesKeycloak`): injiziert
  `DMS_REDIS_URL` (bundled-only, `dms.redisUrl`-Helper) — aktuell nur bei
  `gateway-service` gesetzt (Rate-Limiting, ADR 0097).
- **`storageCronJob`** (P26-S4, ADR 0101): `enabled` steuert, ob
  `templates/storage-cronjob.yaml` einen `CronJob` rendert, der periodisch
  `POST /replication/process-pending` gegen `storage-service`s In-Cluster-
  Service-DNS aufruft (`dms.storageServiceBaseUrl`-Helfer, gleiche
  URL-Formel wie `dms.dependsOnServicesEnv`) — der in ADR 0004/PROGRESS.md
  P20-S6 angekündigte externe Träger für die Replikations-Retry-Queue.
  `replication.schedule`/`.limit`/`.activeDeadlineSeconds`/
  `.{successful,failed}JobsHistoryLimit` sind konfigurierbar, `image.*`
  wählt ein leichtgewichtiges `curlimages/curl`-Utility-Image statt des
  vollen `storage-service`-Images. `principalHeader` setzt einen
  `X-DMS-Principal`-Header (Service-zu-Service-Konvention, aktuell von
  `storage-service` nicht erzwungen, siehe ADR 0101). `verification.enabled`
  ist ein bewusst UNVERDRAHTETER Platzhalter (kein Template nutzt ihn) — der
  reale `GET /object-verify/{key}/all`-Endpunkt verifiziert nur ein
  einzelnes, per Pfad-Parameter übergebenes Objekt, kein Bulk-/Listen-
  Endpunkt für "alle Objekte" existiert aktuell in `storage-service` (siehe
  ADR 0101 für die volle Begründung und einen Gestaltungsvorschlag für eine
  künftige Session).

## Stand

P26-S1 (Grundgerüst + 4 Beispiel-Services: `registry-service`,
`gateway-service`, `document-service`, `storage-service`). P26-S2 hat die
übrigen 28 zustandslosen FastAPI-Services aus `infra/docker-compose.yml`
ergänzt (`services:` hat jetzt 32 Einträge) — dieselben Templates, keine
neuen. Autoscaling ist zusätzlich zu `document-service` (P26-S1) für
`virus-scan-service`, `rendering-service`, `ocr-service` und
`search-service` aktiv (Begründung siehe P26-S2-Session-Report/
`PROGRESS.md`); PDB zusätzlich zu `gateway-service`/`document-service` für
`virus-scan-service`, `rendering-service`, `search-service`.
`templates/_helpers.tpl`s `dms.dependsOnServicesEnv` unterstützt seit P26-S2
neben einfachen Service-Namen auch `{name, envVar}`-Einträge, für die kleine
Minderheit an Services, deren `infra/docker-compose.yml`-Env-Var-Name vom
Standardmuster `DMS_<KEY>_BASE_URL` abweicht (siehe `auth-service`/
`workflow-service` in `values.yaml`).

P26-S3 hat die zustandsbehaftete Infrastruktur (Postgres/Keycloak/MinIO/
NATS/Redis) als echte Deployments+Service(+PVC) gebaut (siehe Struktur/
Konventionen oben) und den seit P26-S1 dokumentierten Secrets-Gap behoben
(ADR 0100). `helm template` rendert jetzt 37 Deployments (32 zustandslose
Services + 5 Infrastruktur-Komponenten), 3 PersistentVolumeClaims (Postgres/
MinIO/NATS — Keycloak/Redis bewusst ohne, siehe jeweiliger `values.yaml`-
Kommentar) und 3 generierte Secrets im Default-Fall.

P26-S4 hat den Storage-Replikations-CronJob ergänzt (`templates/
storage-cronjob.yaml`, siehe ADR 0101) — `helm template` rendert jetzt
zusätzlich 1 `CronJob` (37 Deployments/Services etc. unverändert). Bewusst
nur EIN CronJob statt der im Phase-Briefing angenommenen zwei: der reale
Verifikations-Endpunkt (`GET /object-verify/{key}/all`) braucht einen
konkreten Objekt-`key` und lässt sich nicht blind periodisch "für alle
Objekte" aufrufen — siehe ADR 0101 für Details und einen Vorschlag für eine
künftige `storage-service`-Erweiterung, die das nachrüsten würde. Frontend-
Apps (P26-S5) folgen als letzte Session dieser Phase.
