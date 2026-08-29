{{/* Chart name, overridable. */}}
{{- define "groundtruth.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/* Fully qualified app name. */}}
{{- define "groundtruth.fullname" -}}
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

{{- define "groundtruth.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{- end }}

{{- define "groundtruth.labels" -}}
helm.sh/chart: {{ include "groundtruth.chart" . }}
{{ include "groundtruth.selectorLabels" . }}
{{- if .Chart.AppVersion }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}

{{- define "groundtruth.selectorLabels" -}}
app.kubernetes.io/name: {{ include "groundtruth.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{- define "groundtruth.serviceAccountName" -}}
{{- if .Values.serviceAccount.create }}
{{- default (include "groundtruth.fullname" .) .Values.serviceAccount.name }}
{{- else }}
{{- default "default" .Values.serviceAccount.name }}
{{- end }}
{{- end }}

{{/* Image reference, tag defaulting to the chart appVersion. */}}
{{- define "groundtruth.image" -}}
{{- $tag := .Values.image.tag | default .Chart.AppVersion -}}
{{- printf "%s:%s" .Values.image.repository $tag -}}
{{- end }}

{{/* Name of the Secret to pull env vars from (managed or existing). */}}
{{- define "groundtruth.secretName" -}}
{{- if .Values.secret.existingSecret -}}
{{- .Values.secret.existingSecret -}}
{{- else -}}
{{- include "groundtruth.fullname" . -}}
{{- end -}}
{{- end }}

{{/* PVC name for the `state` volume. */}}
{{- define "groundtruth.statePvcName" -}}
{{- .Values.persistence.state.existingClaim | default (printf "%s-state" (include "groundtruth.fullname" .)) -}}
{{- end }}

{{/* PVC name for the `vaults` volume. */}}
{{- define "groundtruth.vaultsPvcName" -}}
{{- .Values.persistence.vaults.existingClaim | default (printf "%s-vaults" (include "groundtruth.fullname" .)) -}}
{{- end }}
