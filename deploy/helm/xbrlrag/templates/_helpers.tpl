{{/* Common naming + labels for the XbrlRag chart. */}}

{{- define "xbrlrag.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "xbrlrag.fullname" -}}
{{- printf "%s-%s" .Release.Name (include "xbrlrag.name" .) | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "xbrlrag.labels" -}}
app.kubernetes.io/name: {{ include "xbrlrag.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version }}
{{- end -}}

{{- define "xbrlrag.secretName" -}}
{{- if .Values.secrets.existingSecret -}}
{{ .Values.secrets.existingSecret }}
{{- else -}}
{{ include "xbrlrag.fullname" . }}-secrets
{{- end -}}
{{- end -}}

{{/* Shared app env (non-secret from ConfigMap, secret from Secret). */}}
{{- define "xbrlrag.appEnv" -}}
- name: XBRL_DB_HOST
  value: {{ .Values.externalServices.postgres.host | quote }}
- name: XBRL_DB_PORT
  value: {{ .Values.externalServices.postgres.port | quote }}
- name: XBRL_DB_NAME
  value: {{ .Values.externalServices.postgres.name | quote }}
- name: XBRL_DB_USER
  value: {{ .Values.externalServices.postgres.user | quote }}
- name: QDRANT_HOST
  value: {{ .Values.externalServices.qdrant.host | quote }}
- name: QDRANT_PORT
  value: {{ .Values.externalServices.qdrant.port | quote }}
- name: RAGINFER_URL
  value: {{ .Values.externalServices.raginferenceUrl | quote }}
- name: XBRL_DB_PASSWORD
  valueFrom:
    secretKeyRef:
      name: {{ include "xbrlrag.secretName" . }}
      key: postgresPassword
- name: OPENAI_API_KEY
  valueFrom:
    secretKeyRef:
      name: {{ include "xbrlrag.secretName" . }}
      key: openaiApiKey
- name: JINA_API_KEY
  valueFrom:
    secretKeyRef:
      name: {{ include "xbrlrag.secretName" . }}
      key: jinaApiKey
{{- range $k, $v := .Values.config }}
- name: {{ $k }}
  value: {{ $v | quote }}
{{- end }}
{{- end -}}
