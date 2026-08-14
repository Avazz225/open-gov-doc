# 0099 — Helm: ein Chart mit werte-getriebener `services:`-Map statt 34+ Einzel-Charts

**Status:** akzeptiert (P26-S1, siehe `IMPLEMENTATION_PLAN.md`)
**Kontext:** Konzept-übergreifend (Deployment/Betrieb), betrifft `infra/k8s/dms/` (Phase 26, alle Container aus `infra/docker-compose.yml`)

## Entscheidung

`infra/k8s/` bekommt genau **ein** Helm-Chart (`infra/k8s/dms/`, Chart-Name `dms`) für alle
~34 Container aus `infra/docker-compose.yml` — nicht 34+ separate, fast identische
Charts/Templates (ein Chart pro Service wäre die naheliegende, aber bei diesem hohen
Grad an struktureller Gleichheit unnötig repetitive Alternative gewesen). Stattdessen:

- **Eine `services:`-Map in `values.yaml`**, ein Eintrag je Container, mit identischem
  Shape für jeden Service (`enabled`, `image.{repository,tag}`, `port`, `replicas`,
  `resources.{memory,cpu}.{requests,limits}`, `autoscaling.*`, `podDisruptionBudget.*`,
  `env`). Ein neuer Service bedeutet einen neuen Map-Eintrag, nie ein neues Template.
- **Generische Templates** (`templates/deployment.yaml`, `templates/service.yaml`,
  `templates/hpa.yaml`, `templates/pdb.yaml`), die jeweils per `range` über
  `.Values.services` iterieren und nur für Einträge mit `enabled: true` (bzw.
  zusätzlich `autoscaling.enabled`/`podDisruptionBudget.enabled` für HPA/PDB) ein
  Manifest emittieren.
- **`resources.baseline`** als Top-Level-Default, den jeder Service per
  `mergeOverwrite (deepCopy resources.baseline) (service.resources | default dict)`
  selektiv überschreibt (nur die angegebenen Felder, alles andere bleibt Baseline).
- **`postgresql`/`keycloak`/`minio`** (nur diese drei, siehe Begründung) bekommen einen
  `enabled`/`external.*`-Umschalter für "bundled vs. bereits vorhandene externe
  Instanz nutzen" — `nats`/`redis` bleiben bewusst bundled-only ohne diesen Umschalter.
- **`storageService.targets`** als native YAML-Liste (Pendant zu
  `STORAGE_SERVICE_TARGETS`/`DMS_TARGETS`, siehe `BackendTargetConfig` in
  `services/storage-service/src/storage_service/settings.py`), zur Laufzeit per
  `toJson` in die vom Service tatsächlich gelesene Env-Var serialisiert.

Diese Session (P26-S1) baut das Grundgerüst plus vier reale Beispiel-Einträge
(`registry-service`, `gateway-service`, `document-service`, `storage-service`) zum
Beweis, dass Templates+Werte-Struktur end-to-end funktionieren (`helm lint`/
`helm template`, siehe `docs/services/...`-Pendant für diese Session im Report). Die
übrigen ~28 Services (P26-S2), die zustandsbehaftete Infrastruktur als echte
Deployments+PVC (P26-S3), das Storage-Replikations-CronJob (P26-S4) und die
Frontend-Apps (P26-S5) folgen in den nächsten vier Sessions dieser Phase, jeweils nach
demselben Muster.

## Begründung

- **Warum ein Chart statt vieler**: die ~34 Container in `infra/docker-compose.yml`
  sind strukturell fast identisch (FastAPI-Service, ein Port, DSN/NATS/Registry-URL als
  Env-Vars, ein Postgres-Schema) — 34 separate Charts hätten denselben
  Deployment-/Service-/HPA-/PDB-Boilerplate 34-mal dupliziert, mit dem üblichen
  Folgeproblem, dass ein späterer Fix (z. B. eine neue Standard-Env-Var oder ein
  Sicherheitscontext-Default) 34 Dateien statt einer anfassen müsste. Eine
  werte-getriebene Map macht "ein neuer Service" zu einer reinen Daten-Änderung.
- **Warum trotzdem EIN Chart und nicht z. B. Chart-Dependencies/Subcharts pro Service**:
  Subcharts hätten den gleichen Boilerplate-Vervielfachungseffekt (ein `Chart.yaml` +
  Templates-Ordner je Subchart) nur eine Ebene tiefer reproduziert, ohne den
  eigentlichen Vorteil (generische, über Werte parametrisierte Templates) zu heben.
- **Warum `postgresql`/`keycloak`/`minio` einen External-Umschalter bekommen, `nats`/
  `redis` aber nicht**: explizite Nutzervorgabe für diese Session (siehe
  Session-Briefing P26-S1) — Postgres/Keycloak/MinIO sind die drei Infrastruktur-
  Komponenten, bei denen eine reale Installation typischerweise bereits eine verwaltete
  externe Instanz hat (Cloud-DB, zentrales IAM, S3-kompatibler Objektspeicher), NATS und
  Redis dagegen in diesem Projekt bislang ausschließlich als Chart-interne
  Queue-/Cache-Instanz betrieben werden und keinen bekannten externen
  Ersatzbedarf haben.
- **Warum `resources.baseline` + selektiver Merge statt jeden Service vollständig
  auszuschreiben**: die meisten der ~34 Services brauchen keine individuellen
  Requests/Limits — nur eine kleine Minderheit (z. B. `document-service` mit
  Datei-Handling) braucht mehr. Ein Baseline-Default mit selektivem Override hält
  `values.yaml` für den Normalfall knapp, ohne die Möglichkeit einzuschränken, einzelne
  Services abweichend zu konfigurieren.
- **Warum `storageService.targets` als native YAML-Liste statt weiterhin als
  JSON-Freitext-String**: `values.yaml` ist der zentrale, versionierte
  Konfigurationsort dieses Charts — ein YAML-in-JSON-in-YAML-String wäre für
  Reviews/Diffs schlechter lesbar und fehleranfälliger zu editieren als eine native
  YAML-Liste, die das Template per `toJson` erst beim Rendern in das vom Service
  erwartete Format bringt.

## Konsequenzen

- **`values.yaml` ist der einzige Ort, an dem neue Services in P26-S2..S5 auftauchen** —
  wer ein neues Template baut statt einen neuen Map-Eintrag zu ergänzen, weicht von
  dieser Entscheidung ab und sollte das explizit begründen (z. B. ein Service mit
  einer strukturell wirklich andersartigen Deployment-Form).
- **Kein echtes Cluster-Deployment in Phase 26** — Verifikation ausschließlich über
  `helm lint`/`helm template` (siehe Konzept-Referenz Phase 26 Plan-Text). `helm`
  selbst war in dieser Entwicklungsumgebung nicht vorinstalliert und wurde als
  statisches Binary (`get.helm.sh`, v3.15.4) ins Session-Scratchpad geladen — kein
  Root/`apt`-Zugriff nötig, aber auch keine dauerhafte Installation im Image; jede
  künftige Session, die `helm` braucht, muss das ggf. wiederholen oder eine
  dauerhaftere Lösung (z. B. `tools/`) einrichten.
- **`postgresql.auth.password`/vergleichbare Zugangsdaten liegen aktuell als
  Klartext-Value in `values.yaml`** (Parität zum bestehenden Compose-Dev-Setup, bewusst
  dokumentierter v1-Kompromiss) — P26-S3, das die echten bundled Postgres/Keycloak/
  MinIO-Deployments baut, sollte das durch einen echten Kubernetes-`Secret`-Verweis
  ersetzen, statt es unverändert fortzuschreiben.
- **`DMS_POSTGRES_DSN` im `external`-Zweig referenziert `${DMS_POSTGRES_PASSWORD}`** als
  Platzhalter statt eines echten Werts (kein Klartext-Passwort für eine potenziell
  produktive externe DB in `values.yaml`) — dieser Platzhalter wird nicht von Helm
  aufgelöst und muss von P26-S3 durch einen echten Secret-basierten Mechanismus (z. B.
  `envFrom`/`secretKeyRef`) ersetzt werden, bevor der External-Pfad real nutzbar ist.
