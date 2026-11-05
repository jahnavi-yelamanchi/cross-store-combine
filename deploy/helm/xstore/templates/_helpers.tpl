{{- define "xstore.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{- define "xstore.fullname" -}}
{{- if .Values.fullnameOverride }}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- $name := default .Chart.Name .Values.nameOverride }}
{{- if contains $name .Release.Name }}
{{- .Release.Name | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" }}
{{- end }}
{{- end }}
{{- end }}

{{- define "xstore.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{- end }}

{{- define "xstore.labels" -}}
helm.sh/chart: {{ include "xstore.chart" . }}
app.kubernetes.io/name: {{ include "xstore.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}

{{/* Selector labels; call with (dict "ctx" $ "component" "api"). */}}
{{- define "xstore.selectorLabels" -}}
app.kubernetes.io/name: {{ include "xstore.name" .ctx }}
app.kubernetes.io/instance: {{ .ctx.Release.Name }}
app.kubernetes.io/component: {{ .component }}
{{- end }}

{{/* Pod labels: selector labels plus podLabels (e.g. azure.workload.identity/use). */}}
{{- define "xstore.podLabels" -}}
{{ include "xstore.selectorLabels" . }}
{{- with .ctx.Values.podLabels }}
{{ toYaml . }}
{{- end }}
{{- end }}

{{- define "xstore.serviceAccountName" -}}
{{- if .Values.serviceAccount.create }}
{{- default (include "xstore.fullname" .) .Values.serviceAccount.name }}
{{- else }}
{{- default "default" .Values.serviceAccount.name }}
{{- end }}
{{- end }}

{{- define "xstore.image" -}}
{{- printf "%s:%s" .Values.image.repository (default .Chart.AppVersion .Values.image.tag) }}
{{- end }}

{{- define "xstore.dagsterImage" -}}
{{- printf "%s:%s" .Values.dagster.image.repository (default .Chart.AppVersion .Values.dagster.image.tag) }}
{{- end }}

{{/* Shared env; call with (dict "ctx" $ "env" <component env map>). */}}
{{- define "xstore.env" -}}
{{- $env := merge (deepCopy (default dict .env)) .ctx.Values.env }}
{{- range $k := keys $env | sortAlpha }}
{{- $v := get $env $k }}
{{- if not (kindIs "invalid" $v) }}
- name: {{ $k }}
  value: {{ $v | toString | quote }}
{{- end }}
{{- end }}
{{- with .ctx.Values.extraEnv }}
{{ toYaml . }}
{{- end }}
{{- end }}

{{- define "xstore.envFrom" -}}
{{- with .Values.secrets.existingSecret }}
- secretRef:
    name: {{ . }}
{{- end }}
{{- range .Values.secrets.extraExistingSecrets }}
- secretRef:
    name: {{ . }}
{{- end }}
{{- end }}

{{/* Pod-level spec fields shared by all workloads. */}}
{{- define "xstore.podSpecCommon" -}}
serviceAccountName: {{ include "xstore.serviceAccountName" . }}
automountServiceAccountToken: {{ .Values.serviceAccount.automountServiceAccountToken }}
securityContext:
  {{- toYaml .Values.podSecurityContext | nindent 2 }}
{{- with .Values.imagePullSecrets }}
imagePullSecrets:
  {{- toYaml . | nindent 2 }}
{{- end }}
{{- with .Values.nodeSelector }}
nodeSelector:
  {{- toYaml . | nindent 2 }}
{{- end }}
{{- with .Values.tolerations }}
tolerations:
  {{- toYaml . | nindent 2 }}
{{- end }}
{{- with .Values.affinity }}
affinity:
  {{- toYaml . | nindent 2 }}
{{- end }}
{{- end }}

{{- define "xstore.volumes" -}}
- name: data
  emptyDir: {}
- name: tmp
  emptyDir: {}
{{- if .Values.config.enabled }}
- name: config
  configMap:
    name: {{ include "xstore.fullname" . }}-config
{{- end }}
{{- end }}

{{- define "xstore.volumeMounts" -}}
- name: data
  mountPath: /data
- name: tmp
  mountPath: /tmp
{{- if .Values.config.enabled }}
- name: config
  mountPath: /app/policy/policy.yaml
  subPath: policy.yaml
  readOnly: true
{{- range $path, $_ := .Files.Glob "files/mappings/*.yaml" }}
- name: config
  mountPath: /app/stores/mappings/{{ base $path }}
  subPath: mapping-{{ base $path }}
  readOnly: true
{{- end }}
{{- end }}
{{- end }}
