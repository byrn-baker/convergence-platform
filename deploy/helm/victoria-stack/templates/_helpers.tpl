{{- define "victoria-stack.name" -}}{{ .Release.Name }}{{- end -}}
{{- define "victoria-stack.labels" -}}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/part-of: victoria-stack
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end -}}
{{- define "victoria-stack.uid" -}}{{ printf "%s-%s" .Release.Namespace .Release.Name | sha256sum | trunc 12 }}{{- end -}}
