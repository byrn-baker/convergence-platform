# Gateway result correlation: deployment prerequisite

Read-only inspection of installed OpenClaw 2026.10.9 found that mapped
`/hooks/alert` returns an admission acknowledgement with a hook request ID. The
isolated agent runner creates a different lifecycle ID. Passing the hook ID to
`agent.wait` cannot establish completion. That RPC also needs separate Gateway
write authority and only retains results in a bounded in-memory cache.

Direct `/hooks/agent` supports `waitForCompletion: true` on the initial request.
It returns trusted Gateway execution metadata:
`{ok:true, runId, completion:{status, replyDisposition, ...}}`. Terminal status
is `ok`, `error` or `skipped`. This proves execution settled; it does not prove a
particular case mutation succeeded. The existing receiver's synthetic
`{delivery_id, job_id, state}` result format is not the real Gateway contract.

The hook replay cache is memory-only, cleared on restart, and terminal entries
expire after five minutes. A second POST after a lost acknowledgement is not
durable polling, even with the same idempotency key. Preserve the receiver's
durable dispatch checkpoint; ambiguous transport or restart remains `unknown`.
Do not infer a result from a shared case becoming resolved.

## Next implementation boundary

Use a dedicated Gateway investigation adapter. Make one bounded waiting request,
with a deterministic per-delivery session key and idempotency key, no redirects,
and no chat delivery. Keep the same restricted agent, model, timeout and trusted
prompt wrapper as the managed alert mapping. The setup helper should export a
narrow validated dispatch policy, rather than giving the receiver access to the
entire Gateway configuration or its broader operator credential. Reject policy
drift or unsupported mappings before checkpointing a request.

Validate and persist only bounded trusted completion metadata with the receiver
outcome. Present success as agent execution completion unless a separate trusted,
request-correlated case result proves the investigation's intended effect. Do not
store model output in receipts. The HTTP wait must accommodate the bounded agent
budget plus admission allowance, while retaining the existing queue concurrency
and hourly limits. Cancellation, timeout and restart must never cause redispatch.

Qualification needs the actual installed response contract, strict malformed
response checks, silent success, error/skipped completion, lost response, timeout,
cancellation and restart tests. A bounded live investigation remains a separate
operational acceptance check. Durable asynchronous completion beyond this design
needs a Gateway-owned admission/result protocol with external operation identity,
payload-digest conflict handling, durable terminal receipts and narrow read
authority. It cannot be reconstructed from the current hook acknowledgement.

## Evidence

Inspected package sources: `dist/hooks-DZGJ_jS5.mjs` (direct waiting response,
request ID and replay cache), `dist/isolated-agent-CU8sUOoC.mjs` (independent
lifecycle ID), `dist/agent-job-DnnG0jUk.mjs` (volatile wait cache), and installed
`docs/automation/cron-jobs/webhooks.md` / `docs/gateway/config-hooks.md`.
This is version-specific source evidence, not a live model execution or a claim
about future Gateway releases. The full local research record is
`/tmp/receiver-gateway-correlation.md`; no credentials were recorded.
