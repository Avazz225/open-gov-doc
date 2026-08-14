{{/*
_helpers.tpl — Standard-Helm-Namenshelfer PLUS die generischen
Bausteine, über die templates/deployment.yaml, templates/service.yaml,
templates/hpa.yaml und templates/pdb.yaml jeweils per `range` über
.Values.services iterieren (Phase 26, P26-S1 — "ein Chart, keine
34+ Einzeltemplates").
*/}}

{{/* Chart-Basisname. */}}
{{- define "dms.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{/* Voll qualifizierter Release-Name, Standard-Helm-Muster (`helm create`). */}}
{{- define "dms.fullname" -}}
{{- if .Values.fullnameOverride -}}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- $name := default .Chart.Name .Values.nameOverride -}}
{{- if contains $name .Release.Name -}}
{{- .Release.Name | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" -}}
{{- end -}}
{{- end -}}
{{- end -}}

{{- define "dms.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "dms.labels" -}}
helm.sh/chart: {{ include "dms.chart" . }}
{{ include "dms.selectorLabels" . }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end -}}

{{- define "dms.selectorLabels" -}}
app.kubernetes.io/name: {{ include "dms.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end -}}

{{/*
Per-Service-Namenshelfer: <fullname>-<service-name>, z. B.
"myrelease-dms-registry-service". Erwartet dict "root" $ "name" $serviceName.
*/}}
{{- define "dms.serviceFullname" -}}
{{- printf "%s-%s" (include "dms.fullname" .root) .name | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{/* Wie dms.labels, plus app.kubernetes.io/component: <service-name>. */}}
{{- define "dms.serviceLabels" -}}
{{ include "dms.labels" .root }}
app.kubernetes.io/component: {{ .name }}
{{- end -}}

{{/* Wie dms.selectorLabels, plus app.kubernetes.io/component: <service-name>. */}}
{{- define "dms.serviceSelectorLabels" -}}
{{ include "dms.selectorLabels" .root }}
app.kubernetes.io/component: {{ .name }}
{{- end -}}

{{/*
NB: das resources.baseline+Override-Merging (Requirement: ein Service ohne
eigenen resources-Block fällt komplett auf die Baseline zurück, ein Service
MIT eigenem Block überschreibt nur die angegebenen Felder) passiert direkt in
templates/deployment.yaml als `$resources := mergeOverwrite (deepCopy
$.Values.resources.baseline) (default dict $svc.resources)` — bewusst NICHT
als eigenes named template hier, weil `include` in Go-Templates nur Strings
zurückgeben kann, ein zurückgegebenes dict also über `fromYaml` wieder
zurückkonvertiert werden müsste (unnötiger Umweg für einen einzeiligen
Merge-Aufruf).
*/}}

{{/*
Postgres-Verbindungsfelder (Host/Port/Database/Username), respektieren
postgresql.enabled (bundled, Service-DNS "<fullname>-postgresql", siehe
templates/postgresql.yaml) vs. external (postgresql.external.*). Aufgeteilt
in einzelne Helfer statt nur einem fertigen DSN-String, weil
templates/keycloak.yaml dieselben Felder für KC_DB_URL/KC_DB_USERNAME
braucht (Keycloak nutzt dieselbe Postgres-Instanz, siehe
infra/docker-compose.yml-Kommentar dort) — DRY statt zweimal denselben
bundled/external-Branch zu duplizieren.
*/}}
{{- define "dms.postgresHost" -}}
{{- if .Values.postgresql.enabled -}}
{{ include "dms.fullname" . }}-postgresql
{{- else -}}
{{ .Values.postgresql.external.host }}
{{- end -}}
{{- end -}}

{{- define "dms.postgresPort" -}}
{{- if .Values.postgresql.enabled -}}
5432
{{- else -}}
{{ .Values.postgresql.external.port }}
{{- end -}}
{{- end -}}

{{- define "dms.postgresDatabase" -}}
{{- if .Values.postgresql.enabled -}}
{{ .Values.postgresql.auth.database }}
{{- else -}}
{{ .Values.postgresql.external.database }}
{{- end -}}
{{- end -}}

{{- define "dms.postgresUsername" -}}
{{- if .Values.postgresql.enabled -}}
{{ .Values.postgresql.auth.username }}
{{- else -}}
{{ .Values.postgresql.external.username }}
{{- end -}}
{{- end -}}

{{/*
Name/Key des Kubernetes-Secret, das das Postgres-Passwort liefert (P26-S3,
siehe ADR 0100 "existingSecret-if-set-else-generate"). Bundled: entweder
postgresql.existingSecret (bereits vorhandenes Secret, Produktionspfad) oder
das von templates/secrets.yaml aus postgresql.auth.password generierte
"<fullname>-postgresql-secret" (Dev-Default). External: MUSS
postgresql.external.existingSecret gesetzt sein — dieses Chart kennt das
Passwort einer externen Instanz nicht und generiert dafür nichts (siehe
values.yaml-Kommentar).
*/}}
{{- define "dms.postgresSecretName" -}}
{{- if .Values.postgresql.enabled -}}
{{- .Values.postgresql.existingSecret | default (printf "%s-postgresql-secret" (include "dms.fullname" .)) -}}
{{- else -}}
{{- .Values.postgresql.external.existingSecret -}}
{{- end -}}
{{- end -}}

{{/* Fester Key-Name im Postgres-Secret — ein von diesem Chart generiertes
UND ein per existingSecret referenziertes Secret müssen diesen Key liefern. */}}
{{- define "dms.postgresSecretKey" -}}postgres-password{{- end -}}

{{/*
DMS_POSTGRES_DSN — das Passwort selbst kommt NICHT mehr als Klartext in
diesen String (anders als vor P26-S3), sondern über die Kubernetes-eigene
"$(VAR_NAME)"-Referenzsubstitution auf eine zuvor im selben Container-env
definierte DMS_POSTGRES_PASSWORD (siehe dms.baseEnv unten, quellt per
valueFrom.secretKeyRef aus dms.postgresSecretName) — Kubernetes löst
$(VAR_NAME) zur Podstart-Zeit auf, wenn die referenzierte Variable vorher im
selben env-Array steht (offizielles, dokumentiertes k8s-Muster für genau
diesen Fall: einen zusammengesetzten Verbindungsstring aus einem
Secret-Fragment bauen, ohne den ganzen String selbst als Secret pflegen zu
müssen). Ersetzt den vorherigen "${DMS_POSTGRES_PASSWORD}"-Platzhalter aus
P26-S1 (mit geschweiften Klammern von Helm nie aufgelöst UND von Kubernetes
nicht als Referenz erkannt — reine Doku-Attrappe) durch einen tatsächlich
funktionierenden Mechanismus.
*/}}
{{- define "dms.postgresDsn" -}}
postgresql+asyncpg://{{ include "dms.postgresUsername" . }}:$(DMS_POSTGRES_PASSWORD)@{{ include "dms.postgresHost" . }}:{{ include "dms.postgresPort" . }}/{{ include "dms.postgresDatabase" . }}
{{- end -}}

{{/* DMS_NATS_URL — bundled-only, kein external-Escape-Hatch (siehe values.yaml). */}}
{{- define "dms.natsUrl" -}}
nats://{{ include "dms.fullname" . }}-nats:{{ .Values.nats.port }}
{{- end -}}

{{/* DMS_KEYCLOAK_BASE_URL — respektiert keycloak.enabled (bundled) vs. external. */}}
{{- define "dms.keycloakBaseUrl" -}}
{{- if .Values.keycloak.enabled -}}
http://{{ include "dms.fullname" . }}-keycloak:8080
{{- else -}}
{{ .Values.keycloak.external.baseUrl }}
{{- end -}}
{{- end -}}

{{/* Name/Key des Kubernetes-Secret mit dem Keycloak-Admin-Passwort (nur für
die bundled Instanz relevant, siehe templates/keycloak.yaml — ein externes
Keycloak verwaltet seine Admin-Zugangsdaten selbst, dieses Chart braucht sie
dafür nicht). Gleiches existingSecret-Muster wie bei Postgres oben. */}}
{{- define "dms.keycloakSecretName" -}}
{{- .Values.keycloak.existingSecret | default (printf "%s-keycloak-secret" (include "dms.fullname" .)) -}}
{{- end -}}
{{- define "dms.keycloakSecretKey" -}}admin-password{{- end -}}

{{/* DMS_MINIO_ENDPOINT / Platzhalter-Ziel für storageService.targets (P26-S3, neu) —
respektiert minio.enabled (bundled) vs. external. */}}
{{- define "dms.minioEndpoint" -}}
{{- if .Values.minio.enabled -}}
http://{{ include "dms.fullname" . }}-minio:9000
{{- else -}}
{{ .Values.minio.external.endpoint }}
{{- end -}}
{{- end -}}

{{/* Name/Key des Kubernetes-Secret mit dem MinIO-Root-Passwort. Gleiches
existingSecret-Muster wie bei Postgres oben (bundled: generiert oder
existingSecret; external: MUSS minio.external.existingSecret gesetzt sein). */}}
{{- define "dms.minioSecretName" -}}
{{- if .Values.minio.enabled -}}
{{- .Values.minio.existingSecret | default (printf "%s-minio-secret" (include "dms.fullname" .)) -}}
{{- else -}}
{{- .Values.minio.external.existingSecret -}}
{{- end -}}
{{- end -}}
{{- define "dms.minioSecretKey" -}}root-password{{- end -}}

{{/* DMS_REDIS_URL (P26-S3, neu) — bundled-only, kein external-Escape-Hatch
(siehe values.yaml, gleiche Begründung wie bei NATS). Kein Passwort/Secret
nötig, Redis läuft hier ohne Auth (analog infra/docker-compose.yml). */}}
{{- define "dms.redisUrl" -}}
redis://{{ include "dms.fullname" . }}-redis:{{ .Values.redis.port }}/0
{{- end -}}

{{/*
Baseline-Env-Set, das JEDER Service bekommt (Pendant zu den in praktisch
jedem infra/docker-compose.yml-Service wiederholten DMS_INSTALLATION_ID/
DMS_POSTGRES_DSN/DMS_NATS_URL/DMS_REGISTRY_SERVICE_BASE_URL/DMS_SELF_ADDRESS-
Zeilen). Erwartet dict "root" $ "name" $serviceName.
*/}}
{{- define "dms.baseEnv" -}}
- name: DMS_INSTALLATION_ID
  value: {{ .root.Values.global.installationId | quote }}
- name: DMS_INSTALLATION_DISPLAY_NAME
  value: {{ .root.Values.global.installationDisplayName | quote }}
{{/* Seit P26-S3: das Passwort selbst kommt über secretKeyRef, DMS_POSTGRES_DSN
baut es per k8s-"$(VAR)"-Substitution ein statt es literal einzubetten (siehe
dms.postgresDsn/dms.postgresSecretName-Kommentar oben). */}}
- name: DMS_POSTGRES_PASSWORD
  valueFrom:
    secretKeyRef:
      name: {{ include "dms.postgresSecretName" .root }}
      key: {{ include "dms.postgresSecretKey" .root }}
- name: DMS_POSTGRES_DSN
  value: {{ include "dms.postgresDsn" .root | quote }}
- name: DMS_NATS_URL
  value: {{ include "dms.natsUrl" .root | quote }}
- name: DMS_REGISTRY_SERVICE_BASE_URL
  value: {{ printf "http://%s-registry-service:8000" (include "dms.fullname" .root) | quote }}
- name: DMS_SELF_ADDRESS
  value: {{ printf "http://%s:8000" (include "dms.serviceFullname" .) | quote }}
{{- end -}}

{{/*
Env-Vars für jeden Eintrag in services.<name>.dependsOnServices — pro
Eintrag "foo-service" wird DMS_FOO_SERVICE_BASE_URL=http://<fullname>-foo-service:8000
gesetzt (Pendant zu den einzeln in infra/docker-compose.yml gepflegten
DMS_<X>_SERVICE_BASE_URL-Zeilen). Erwartet dict "root" $ "deps" $svc.dependsOnServices.

Seit P26-S2: ein Eintrag darf statt eines bloßen String auch ein Dict
{name: <service-key>, envVar: <ENV_VAR_NAME>} sein, für die kleine
Minderheit an Services, deren infra/docker-compose.yml-Env-Var-Name NICHT
dem Standardmuster DMS_<KEY>_BASE_URL folgt (historisch gewachsene
Compose-Namen, z. B. auth-service/workflow-service ->
DMS_INSTALLATION_GATEWAY_BASE_URL statt DMS_GATEWAY_SERVICE_BASE_URL für
gateway-service, oder DMS_FEDERATION_HUB_BASE_URL statt
DMS_FEDERATION_HUB_SERVICE_BASE_URL für federation-hub-service) - siehe
services.auth-service/workflow-service in values.yaml für reale Beispiele.
Der berechnete URL-Wert (http://<fullname>-<service-key>:8000) ist in beiden
Formen identisch, nur der emittierte Env-Var-NAME unterscheidet sich.
*/}}
{{- define "dms.dependsOnServicesEnv" -}}
{{- range .deps }}
{{- if kindIs "map" . }}
- name: {{ .envVar }}
  value: {{ printf "http://%s-%s:8000" (include "dms.fullname" $.root) .name | quote }}
{{- else }}
- name: {{ printf "DMS_%s_BASE_URL" (. | replace "-" "_" | upper) }}
  value: {{ printf "http://%s-%s:8000" (include "dms.fullname" $.root) . | quote }}
{{- end }}
{{- end -}}
{{- end -}}

{{/*
storage-service-spezifisch: serialisiert storageService.targets (natives
YAML) zurück in DMS_TARGETS (siehe values.yaml-Kommentar zur Namensklärung
DMS_TARGETS vs. STORAGE_SERVICE_TARGETS), plus DMS_WRITE_STRATEGY/
DMS_QUORUM_COUNT. Nur eingebunden, wenn services.<name>.storageTargetsEnv
true ist. Erwartet den Chart-Root ($).
*/}}
{{- define "dms.storageServiceTargetsEnv" -}}
{{/*
Seit P26-S3: storageService.targets[].endpoint_url darf den Platzhalter
"__DMS_MINIO_ENDPOINT__" enthalten (siehe secondary-s3-Ziel in values.yaml) -
wird hier per Text-Replace durch dms.minioEndpoint ersetzt (respektiert
minio.enabled bundled/external), statt wie vor dieser Session den reinen
Compose-Hostnamen "minio" fest einzubrennen (der im Chart nicht existiert -
echter Bug, den erst der reale templates/minio.yaml-Service in dieser Session
sichtbar gemacht hat). toJson liefert den kompletten JSON-String, der
Platzhalter steht darin wie jeder andere Feldwert als Text - kein
JSON-Escaping-Sonderfall, da dms.minioEndpoint selbst kein JSON-Sonderzeichen
enthält.
*/}}
- name: DMS_TARGETS
  value: {{ toJson .Values.storageService.targets | replace "__DMS_MINIO_ENDPOINT__" (include "dms.minioEndpoint" .) | quote }}
- name: DMS_WRITE_STRATEGY
  value: {{ .Values.storageService.writeStrategy | quote }}
- name: DMS_QUORUM_COUNT
  value: {{ .Values.storageService.quorumCount | quote }}
{{- end -}}
