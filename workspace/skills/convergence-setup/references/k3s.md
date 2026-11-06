# K3s/Helm setup and existing-stack reuse

Use `docs/convergence/k3s.md` from the verified checkout as the canonical procedure.
Resolve the helper paths relative to this skill; pass the checkout with `--repo`.
The guide includes image delivery alternatives, storage explanations and access.

## Fresh release

1. Discover explicit context, namespace, release, site, node architectures, storage
   class, capacity and image delivery. Read existing resources before assuming a
   new deployment. Do not change the global current context. Creating a K3s cluster
   changes a host and is separate from installing into an existing cluster.
2. Run `scripts/k3s.py --repo "$repo" preflight --context "$context"`. Resolve
   unavailable access, unready nodes or missing storage before installation.
3. Build the API with a revision-specific tag. Import on every eligible native K3s
   node, use k3d image import for k3d, or push to an authorized registry. Verify CPU
   architecture and pull credentials. A host Docker image is not automatically in
   the cluster's containerd store.
4. Register the Victoria chart repository as shown in the guide, then build both
   Helm dependency layers. Prepare the private bundle using
   `scripts/k3s.py --repo "$repo" prepare` with every required target option.
   Standalone Grafana is enabled for a fresh release; use `--no-grafana` when
   reusing shared Grafana. Review non-secret values and resource requests.
5. Run `scripts/k3s.py --repo "$repo" install --bundle "$bundle"`. It uses the
   explicit target and preserves a matching Secret. Do not bypass a mismatch by
   overwriting database passwords. Diagnose failed Helm readiness in place.
6. Run `scripts/verify.py --repo "$repo" --bundle "$bundle"`, then the authorized
   `--canary` acceptance check. Confirm case persistence after restart and the
   collector-to-VictoriaLogs round-trip. Open Grafana through loopback forwarding.
7. Follow `docs/convergence/operations.md` for HUD connectivity, telemetry, backup,
   repeat upgrades and a durable access plan. Temporary forwards are not a
   permanent integration. Report which layers remain unverified.

The fresh bundle overrides historical Longhorn defaults with the selected storage
class. K3s local-path is node-local, not replication or a backup. Do not translate
an existing Helm release to legacy Kustomize or adopt resources into another
controller without a migration plan.

## Reuse or migrate

Inventory releases, PVCs, Secrets, Grafana ownership, collector endpoints and
current producers/consumers. Keep credentials private. Separate a parallel new
release from a cutover: first verify new storage and canaries, then backfill or
restore selected history, verify counts/time ranges, and switch clients in a
controlled order. Avoid duplicate collectors or investigations. Keep rollback
routes and old data until the new path is verified.

Shared Grafana should retain its current owner. Prefer the external provisioning
bundle or manually add distinct datasource names/UIDs. Do not replace other
projects' defaults. Review imported dashboards for site filters and unavailable
feeds. Argo CD adoption is a separate ownership operation, not a prerequisite for
a fresh Helm installation.

Home profiles and historical cutover runbooks are deployment records. Their node
addresses, namespaces, registry account and volume names are not portable defaults.
