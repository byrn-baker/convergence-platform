# Investigation dispatch rollout sequence

This sequence activates the qualified code after release publication. It does
not activate itself. Keep the working native HUD installation in place.

1. Pin the release's API/HUD image digests and receiver checkout. Back up
   PostgreSQL and the stopped receiver SQLite database, including WAL state via
   the supported SQLite backup mechanism. Rehearse the additive migrations and
   restricted grants using the release image before touching the live database.
2. Export/check the managed Gateway policy with `setup/reviewer_gateway.py`.
   Confirm the qualified Gateway protocol version (OpenClaw 2026.10.9), allowed
   alert agent and session prefix. Put the policy path/digest in the private
   receiver environment. This reads no Gateway operator credential.
3. Prepare receiver TLS on the existing host, on a distinct port, with a server
   SAN matching the relay's HTTPS origin. Issue a dedicated delivery token and
   trust bundle. The ordinary HTTP listener and its credential remain in use.
   Run `setup/receiver.py preflight --env-file ...`. Do not start a second receiver
   against its inbox. Confirm external-process RAG and the explicit control-group
   stop policy. Prefer a drained RAG/write queue for the cutover and inspect
   snapshot state before stopping. Listener health alone is not this check.
4. Stop the old receiver through systemd, confirm its process group is gone,
   inspect the stopped queue for interrupted snapshot/write jobs, then install
   and start the qualified version with the same queue path and RAG runtime.
   If a write was interrupted, keep the receiver stopped until its partial
   storage and pending job have been reconciled; startup can automatically retry
   pending work. Do not assume only a forced kill can interrupt a write. Check
   ordinary authenticated HTTP and the restricted trusted HTTPS listener after
   startup. Preserve received work and dispatch checkpoints.
5. Run the PostgreSQL migration with schema-owner credentials, then apply the
   existing runtime grants plus `permissions-dispatch.sql` for the runtime and
   a separate restricted relay role. Do not put schema-owner credentials in a
   long-lived Pod. Store relay database credentials and delivery token/CA in two
   distinct Secrets. Keep reviewer investigation acceptance disabled initially.
6. Reconcile the reviewer API/HUD images and relay deployment through GitOps.
   Confirm certificate trust and network reachability from the relay's actual
   Pod. Confirm its restricted role can lease/read/update delivery records but
   cannot create commands, read accounts/cases or alter audit history.
7. Enable acceptance for a bounded commissioning window and submit one clearly
   labeled synthetic case through the named reviewer's HTTPS session. Verify one
   named note, one acceptance, one receiver job, one Gateway execution and a
   terminal receipt carrying stored Gateway metadata. Inspect actual case
   findings separately from the execution status. Replaying the same request
   must return the original receipt without another hook. Disable new acceptance
   if this proof fails; preserve the unknown receipt for reconciliation.
8. Restart the receiver and relay through their supported service managers and
   verify the same receipt, healthy ordinary alert delivery and no repeated hook.
   Check the owner's existing native HUD still displays live telemetry. Record
   image digests, service state and proof in the private installation runbook.

Rollback first disables new acceptance. Stop relay delivery deliberately if
needed, retain both databases and restore the prior service configuration only
after the upgraded receiver has stopped. The new SQLite receipt columns are
additive, but the older dispatch implementation writes positional INSERTs and
must not be resumed against the upgraded receipt schema for reviewer admission.
Keep reviewer admission disabled during downgrade; preserve receipt data and
ordinary alert processing. Do not delete new receipt columns or accepted work
as an automatic rollback step.
