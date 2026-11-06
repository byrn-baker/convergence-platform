{{- define "convergence.labels" -}}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/part-of: convergence
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end -}}
{{- define "convergence.secret" -}}{{ required "Set existingSecret to an independently managed application Secret" .Values.existingSecret }}{{- end -}}
