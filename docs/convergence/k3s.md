# Install Convergence on K3s with Helm

This is the recommended persistent deployment. Each Helm release owns its API,
PostgreSQL case database, VictoriaMetrics, VictoriaLogs, Loki, vmagent, vmalert,
Alertmanager, OpenTelemetry collector, blackbox exporter and pushgateway. The
fresh-install bundle also enables a private Grafana. Device adapters, IDS capture
and model investigations are separate choices.

The chart creates namespace-scoped resources and ClusterIP services. The guide
uses loopback port-forwarding first, so you can verify the stack before deciding
how to expose it to your NetClaw host or other users.

## 1. Check prerequisites

You need:

- An existing, supported **K3s release** meeting the chart's Kubernetes 1.29+ constraint,
  with Ready nodes and a working dynamic
  storage class. Review the [K3s requirements](https://docs.k3s.io/installation/requirements)
  and [quick start](https://docs.k3s.io/quick-start) if creating a new Linux cluster.
  Installing K3s changes the host; it is a separate infrastructure step.
- `kubectl`, Helm 3.19+ or Helm 4, Git and Python 3.10+ on your administration machine.
- Docker or another OCI builder for the API image, plus a way to deliver that image
  to **every node where its pod can run**.
- Permission to create a namespace, Secret and the chart's namespaced workloads.
  Preflight also reads nodes and storage classes.
- A checkout containing this add-on, with outbound access to the chart and image
  registries. Air-gapped mirrors are not automated by this guide.

Plan initially for 4 available CPU cores, 8 GiB RAM and at least 20 GiB free disk
for the small installation plus image/runtime overhead. This is a starting budget,
not a load-tested capacity guarantee. Measure ingestion and retention before
adding IDS or high-volume flows. The small generated profile requests 12 GiB of
PVC capacity with Grafana; provisioned capacity and actual disk use differ.

K3s includes the [`local-path` provisioner](https://docs.k3s.io/add-ons/storage).
Its volumes live on a particular node: this is persistent storage, **not replicated
storage or a backup**. Use your existing suitable storage class when you need a
different durability policy. Do not copy another installation's Longhorn settings
unless Longhorn is actually installed.

Choose the intended context explicitly; these commands do not switch it globally:

```bash
kubectl config get-contexts
context="lab"                 # replace with the exact context from the list
namespace="convergence"
release="convergence"
bundle="$HOME/.local/share/convergence/k3s-lab"
python3 workspace/skills/convergence-setup/scripts/k3s.py preflight --context "$context"
```

The output must show Ready nodes, your storage class and Helm. Inspect available
capacity and existing workloads before continuing. Use an unused release and
namespace for a parallel trial; existing installations follow [operations](operations.md).

## 2. Build and deliver the API image

There is no assumed public prebuilt Convergence API image. Build from the checkout
and use a revision-specific tag:

```bash
image="netclaw-convergence-api:$(git rev-parse --short=12 HEAD)"
docker build -t "$image" ui/convergence-api
```

Choose **one** delivery method:

**A. Native K3s, same CPU architecture as your builder.** Save the image and import
it on every eligible node. Copy the archive to remote nodes first when needed.
The import command below must run on the K3s node, not merely on your laptop.

```bash
docker save "$image" -o convergence-api-image.tar
# On each eligible K3s node, using the transferred archive:
sudo k3s ctr images import convergence-api-image.tar
```

**B. Registry, especially for remote or mixed-architecture clusters.** Use a
registry/repository you can push to, and ensure cluster nodes can pull from it.
For a builder configured for both architectures:

```bash
image="registry.example.com/your-project/convergence-api:$(git rev-parse --short=12 HEAD)"
docker buildx build --platform linux/amd64,linux/arm64 --tag "$image" --push ui/convergence-api
```

Replace the example registry. Private registries require an image-pull Secret in
the selected namespace and `imagePullSecrets` in `values.json`. Alternatively,
constrain the API to matching nodes with `api.nodeSelector`; an AMD64-only image
will not run on ARM64. See the [qualification record](verification.md) before
assuming a platform was tested.

For a disposable **k3d** cluster, use `k3d image import "$image" -c CLUSTER_NAME`
instead of native-node import. k3d runs K3s in Docker; it is useful for development,
not a replacement for a backup policy.

## 3. Prepare and review the release

Run from the checkout root, retaining the variables from above:

```bash
helm repo add victoria-metrics https://victoriametrics.github.io/helm-charts/
helm dependency build deploy/helm/victoria-stack
helm dependency build deploy/helm/convergence
python3 workspace/skills/convergence-setup/scripts/k3s.py prepare \
  --output "$bundle" --context "$context" --namespace "$namespace" \
  --release "$release" --site lab --storage-class local-path --image "$image"
```

The bundle contains:

| File | Purpose |
|---|---|
| `values.json` | Non-secret Helm settings: image, site, storage, retention, optional Grafana |
| `secret.json` | Generated API, PostgreSQL and Grafana credentials; keep private |
| `deployment.json` | Exact target identity for repeatable setup |
| `hud.env` | Matching credentials for an existing NetClaw HUD; URLs initially assume local port-forwards |

Review `values.json` before installing. The generated settings use small volumes,
7-day Victoria retention and 72-hour Loki retention. Increase capacity for your
workload; retention limits time, not maximum disk consumption. Re-running prepare
preserves credentials and edits. Different target options require a different
bundle. **Do not generate new PostgreSQL credentials for existing data.**

To reuse shared Grafana, pass `--no-grafana` during preparation and follow the
[shared Grafana instructions](operations.md#reuse-existing-grafana). The default
chart itself does not install another Grafana; the fresh bundle explicitly enables it.

## 4. Install and verify

```bash
python3 workspace/skills/convergence-setup/scripts/k3s.py install --bundle "$bundle"
kubectl --context "$context" -n "$namespace" get pods,pvc
python3 workspace/skills/convergence-setup/scripts/verify.py --bundle "$bundle"
```

Installation waits up to ten minutes for readiness. The helper checks the target
storage class and preserves a matching existing Secret; mismatched credentials
stop the install. A failed install is left available for diagnosis and retry.
It does not silently remove data or fall back to your current Kubernetes context.

Verification opens temporary loopback port-forwards, checks site access and
authentication boundaries, queries the API probe through VictoriaMetrics and
Grafana, and closes the forwards. Allow one scrape interval (30 seconds) after
first startup. All checks should pass.

For a labeled case write/read/archive and an OTLP log round-trip through the
collector into VictoriaLogs:

```bash
python3 workspace/skills/convergence-setup/scripts/verify.py --bundle "$bundle" --canary
```

The canaries remain identifiable test evidence. They do not send external
notifications, inspect packets or launch a model investigation.

## 5. Open the dashboard

In a separate terminal, keep this command running:

```bash
kubectl --context "$context" -n "$namespace" port-forward --address 127.0.0.1 \
  "service/$release-grafana" 3300:3000
```

Open **http://127.0.0.1:3300**, username `admin`. Read
`stringData.GRAFANA_ADMIN_PASSWORD` from your private `secret.json` locally.
Open **Dashboards → Convergence installation health**. Core targets and the API
probe should show `1`. Network/IDS panels are not populated by installation alone.

If NetClaw runs on another machine, `127.0.0.1` means that other machine. Run a
forward on the NetClaw host or design private, authenticated service access;
see [operations](operations.md). Do not expose the unauthenticated storage APIs
with a public NodePort or ingress just to make a dashboard work.

Continue with [NetClaw integration, telemetry, backup and upgrades](operations.md).
