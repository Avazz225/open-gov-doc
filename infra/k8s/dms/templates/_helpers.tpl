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

{{/* DMS_POSTGRES_DSN — respektiert postgresql.enabled (bundled) vs. external. */}}
{{- define "dms.postgresDsn" -}}
{{- if .Values.postgresql.enabled -}}
postgresql+asyncpg://{{ .Values.postgresql.auth.username }}:{{ .Values.postgresql.auth.password }}@{{ include "dms.fullname" . }}-postgresql:5432/{{ .Values.postgresql.auth.database }}
{{- else -}}
postgresql+asyncpg://{{ .Values.postgresql.external.username }}:${DMS_POSTGRES_PASSWORD}@{{ .Values.postgresql.external.host }}:{{ .Values.postgresql.external.port }}/{{ .Values.postgresql.external.database }}
{{- end -}}
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
*/}}
{{- define "dms.dependsOnServicesEnv" -}}
{{- range .deps }}
- name: {{ printf "DMS_%s_BASE_URL" (. | replace "-" "_" | upper) }}
  value: {{ printf "http://%s-%s:8000" (include "dms.fullname" $.root) . | quote }}
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
- name: DMS_TARGETS
  value: {{ toJson .Values.storageService.targets | quote }}
- name: DMS_WRITE_STRATEGY
  value: {{ .Values.storageService.writeStrategy | quote }}
- name: DMS_QUORUM_COUNT
  value: {{ .Values.storageService.quorumCount | quote }}
{{- end -}}
