#!/usr/bin/env bash
# Install Argo CD into an explicit cluster context. Pinned chart; no ingress, so
# reach the UI with:  kubectl --context CTX -n argocd port-forward svc/argocd-server 8080:80
set -euo pipefail
context=${1:?usage: install.sh KUBE_CONTEXT}
: "${KUBECONFIG:?Set KUBECONFIG to the file that holds this context}"
here=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
version=${ARGOCD_CHART_VERSION:-10.11.6}
helm repo add argo https://argoproj.github.io/argo-helm --force-update >/dev/null
helm upgrade --install argocd argo/argo-cd --version "$version" \
  --kube-context "$context" --namespace argocd --create-namespace \
  -f "$here/values.yaml" --wait --timeout 12m
kubectl --context "$context" -n argocd rollout status deploy/argocd-server --timeout=5m
echo "Argo CD $version installed in context $context (namespace argocd)."
