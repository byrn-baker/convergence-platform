# Plan

Follow repository spec -> plan -> tasks -> implementation lifecycle explicitly.
T001 ingestion worker owns services/ids-ingest modules/tests/config/Dockerfile.
Use a host_events module for validation, bindings, status and case construction;
reuse Store delivery rows with host-prefixed kinds and server identity envelope.
T002 agent worker owns deploy/convergence/host-agent and its synthetic tests.
Use stdlib and a bounded atomic disk queue, HTTPS-only transport and systemd example.
T003 coordinator owns docs, spec ledger, contract-suite registration and integration.
This includes the existing alert receiver's security-category classification and
a synthetic case-processing test proving host evidence remains T0/observe-only.
T004 independent verifier reads the integrated diff and executes synthetic checks.
No worker deploys, publishes, contacts a VM/provider, or edits another worker's files.

Operator-view follow-up: specify acceptance above before implementation; the
coordinator follows the checked-in Spec Kit artifacts manually because the
referenced slash-command files are absent. One API worker owns the new status
reader, additive Security response and backend tests. An independent UI worker
owns the HUD/API dashboard renderers and UI fixtures. The coordinator owns
documentation, contract-suite registration, release integration and deployment.
Workers do not deploy or publish. Independently review site/privacy boundaries and
rendering before a managed release; preserve the existing guest, IDS enrollment,
sensor configuration and all unrelated working trees.

No new MCP, skill, environment variable, installer component, tenant service or
platform release is introduced. Catalog/SOUL/TOOLS/registration changes are not
applicable to this presentation follow-up within the draft pilot capability.

Prototype has no MCP/model integration changes: existing case pipeline is the seam.
README/service/runbook docs applicable; no SOUL/TOOLS/HUD/tool count/catalog change
because no new installed tool or supported UI is being introduced. Full rollout,
server-side resource inventory ingestion and trusted boundary collection deferred.
