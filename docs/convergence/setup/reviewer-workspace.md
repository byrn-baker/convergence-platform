# Authenticated site workspace and JEV requests

The dedicated HTTPS reviewer can optionally host Convergence's shared site views.
The existing NetClaw HUD remains independent. This does not add remote ownership
of NetClaw, its terminals, model settings, chat or configuration.

Set `reviewer.telemetryEnabled: true` for the Helm reviewer API to advertise site
telemetry. It uses the chart's site configuration and internal metrics, logs and
Alertmanager endpoints. For IDS, VM Security and guest heartbeats, set
`reviewer.telemetryIdsSecret` to a Secret containing only the needed
`IDS_STATUS_URL`, `IDS_STATUS_TOKEN` and `ids-ca.crt` keys. The Pod references
those keys explicitly; it does not import a general application credential set.
An empty or unavailable source displays a coverage gap, not a healthy result.
Custom installations set the corresponding telemetry service environment and
`REVIEWER_TELEMETRY_ENABLED=1` directly. Configure the reviewer database role with
a bounded statement timeout (12 seconds) as well as its existing privileges.

Per-user grants authorize every site read before acquisition and again before
delivery. External reads do not hold the account/grant transaction open. The
browser discards private state on logout, expiry and authority changes. Selecting
a case uses the existing attributed disposition and investigation operations.
The shared site view has no host administration controls. With telemetry disabled,
the original case-review entry point remains available.

## JEV ownership and enablement

An operator requests the existing shared assessment for a case. Their receipt says
whether it started, joined or reused the assessment. Two reviewers get two named
receipts and share one assessment/provider attempt. Replaying an operation never
selects a fresh budget. The existing JEV abstraction, case budget identity, global
daily budget and review worker remain authoritative. A completed request may
contain budget exhaustion, provider failure or unavailable evidence; it is not
proof of compromise, successful investigation or permission to remediate.

The API, relay and receiver have separate opt-in controls:

| Component | Helm / environment |
| --- | --- |
| New reviewer acceptance | `reviewer.jevEnabled` / `REVIEWER_JEV_ENABLED=1` |
| Relay delivery and receipt collection | `reviewer.relay.jevEnabled` / `REVIEWER_JEV_DELIVERY_ENABLED=1` |
| Receiver new assessment admission | `REVIEWER_JEV_ENABLED=1` |
| Existing provider and budget configuration | Existing `CONVERGENCE_JEV_*` / `JEV_*` configuration |

The relay itself must also be enabled. Disabling new acceptance leaves accepted
work and receipt reads intact. Keep the relay running to drain it. A provider
result is persisted before case publication; publication failure cannot purchase
another evaluation. Canonical assessment results and request associations survive
ordinary queue cleanup. Oversized/unsupported display results are explicitly
unavailable while the canonical record remains retained.

## Qualification and rollout

Before enabling features, apply the additive reviewer migration (`jev.sql`) and
updated base/dispatch grants **in one administrative transaction**. The operation
registry also protects existing disposition and investigation writers, so never
install its triggers without their runtime role privileges. Rehearse against a
database copy, including a second application. Historical conflicting operation
IDs require reconciliation; migration must not silently choose a command.

Build the reviewer image with context `ui/convergence-hud` and Dockerfile
`ui/convergence-hud/reviewer/Dockerfile`, so shared view assets are included.
Qualify the actual image, real PostgreSQL/mTLS/browser/relay/receiver path with a
counted synthetic provider, and compare live read-only telemetry with the working
HUD before cutover. No production provider call is implied by fixture success.

Rollback first disables new acceptance, drains accepted JEV work and retains
PostgreSQL, SQLite receipts, assessment results and the JEV budget ledger. Keep
the additive schema/role grants when restoring older compatible images; removing
the audit registry or canonical records is not part of rollback. Record exact
pins and backups before deployment. The ordinary native HUD is not migrated by
this increment.
