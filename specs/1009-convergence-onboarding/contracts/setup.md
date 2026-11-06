# Setup CLI contract

Keep existing `preflight`, `prepare`, `check` Docker behavior compatible.
K3s preparation requires explicit context, namespace, release, site, storage class
and image. Commands bind the context on every Kubernetes/Helm call. Values contain
Secret names only. Private Secret material is sent from a file/stdin; stdout gives
paths and bounded status, never credentials or resolved manifests.

Checks return nonzero on failure, distinguish missing tool/permission/connectivity,
and do not mutate devices, call models or silently fall back to another cluster.
Repeat preparation retains credential bytes and refuses conflicting options.
