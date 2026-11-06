# Research and decisions

## Current gaps
The current overview says Docker is primary; Helm docs use a home-only kubeconfig,
Longhorn, a named node, private image distribution and an existing Grafana.
The setup skill describes Kustomize and explicitly lacks a K3s generator. The
Docker generator is already isolated and credential-preserving but exposes only
five core services. These are real onboarding gaps, not prose-only problems.

## K3s
Decision: Helm is the primary supported packaging; require a chosen context and
storage class, default to the K3s local-path class in the tutorial, retain an
explicit HA/storage caveat. Local-path is node-local rather than replicated.
References: https://docs.k3s.io/quick-start ;
https://docs.k3s.io/installation/requirements ;
https://docs.k3s.io/add-ons/storage (checked 2026-10-30).
Alternative: old Kustomize home overlay — retained as legacy, not new-user default.

## Docker
Decision: use a Linux-container Compose engine. Linux Engine + Compose and Docker
Desktop are separate installations/contexts. Mac and Windows users follow official
Desktop/WSL guidance rather than Linux systemd commands. Existing operator services
are not assumed or installed silently.
References: https://docs.docker.com/compose/install/ ;
https://docs.docker.com/desktop/setup/install/windows-install/ (2026-10-30).

## Contribution boundary
Upstream CONTRIBUTING requires a numbered spec and prior substantial-scope
agreement. The public user fork belongs to automateyournetwork/netclaw's network.
A clean spec-only draft can request scope review without exposing local deployment
history. Full implementation is not ready merely because a home deployment works.
Independent planning audits cover Docker portability and upstream port dependencies.

## Original project attribution

The author supplied [byrn-baker/Convergence](https://github.com/byrn-baker/Convergence/tree/fff81b52fdd9b792e375208abfa8d091adf9949a) on 30 October 2026. Reviewed its tree, README and MIT license at `fff81b52fdd9b792e375208abfa8d091adf9949a`. It contains the standalone observability stack, discovery and dashboards, but no copy of the NetClaw API. Preserve the original `Copyright (c) 2026 Convergence Project` notice separately from the API's author-confirmed `network-guardian-web` ancestry. See the [provenance record](../../docs/convergence/provenance.md). This source review does not replace per-asset attribution checks during the focused upstream port.
