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
└── templates/
    ├── _helpers.tpl      # Namens-/Label-Helfer + generische Env-/Resource-Bausteine
    ├── deployment.yaml    # EIN Template für ALLE Services (range über services:)
    ├── service.yaml       # dito
    ├── hpa.yaml            # HorizontalPodAutoscaler, nur wo autoscaling.enabled
    ├── pdb.yaml             # PodDisruptionBudget, nur wo podDisruptionBudget.enabled
    └── NOTES.txt
```

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
  gebündelte Instanz (P26-S3 baut die echten Deployments+PVC), `enabled:
  false` + `external.*` = bereits vorhandene externe Instanz nutzen. `nats`/
  `redis` sind bewusst bundled-only ohne diesen Umschalter.
- **`storageService.targets`**: native YAML-Liste (Pendant zu
  `BackendTargetConfig` in `services/storage-service`), wird zur Laufzeit per
  `toJson` in die vom Service gelesene `DMS_TARGETS`-Env-Var serialisiert
  (nur für Services mit `storageTargetsEnv: true`, aktuell `storage-service`).

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
`workflow-service` in `values.yaml`). Zustandsbehaftete Infrastruktur als
echte Deployments+PVC (P26-S3), Storage-Replikations-CronJob (P26-S4) und
Frontend-Apps (P26-S5) folgen in den nächsten Sessions dieser Phase.
