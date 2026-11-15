# Receiver HTTPS and shared lifecycle

Implement an optional dedicated TLS listener in the existing receiver process.
The current `ALERT_RECEIVER_HOST/PORT` HTTP endpoint continues serving existing
callers. One FastAPI application lifespan owns the SQLite inbox and worker pool;
listener instances must never start independent lifespans. Keep the new service
boundary in a separate module rather than expanding business routes with socket
management.

Configuration is opt-in using `REVIEWER_HTTPS_HOST`, `REVIEWER_HTTPS_PORT`,
`REVIEWER_HTTPS_CERT_FILE`, `REVIEWER_HTTPS_KEY_FILE`. No settings means legacy
HTTP-only operation. Partial/invalid configuration fails startup before workers
or listeners run. Use explicit numeric bind IP and a distinct port. TLS requires
the dedicated `REVIEWER_DELIVERY_TOKEN`, distinct from the ordinary receiver token,
and a certificate/key pair validated before queue startup. Never log secrets.

TLS serves only `/reviewer/commands` and its receipt subpaths (with existing strict
routing/authorization), plus a minimal listener health endpoint. It exposes no
legacy administrative or webhook routes. HTTP must reject reviewer delivery even
with the correct credential or forged forwarding headers. Disable proxy-header
interpretation on both listeners. Reject websocket and unrecognized scope/path
requests. The runtime owns signals; one failed listener ends both listeners and
the shared lifespan. Pre-bind all sockets before starting the shared lifespan so
port conflicts cannot leave a partially running receiver. Gracefully drain both
listeners before shutting down workers/database, with bounded shutdown.

Acquire an advisory exclusive queue-owner lock for the lifetime of the application
before creating JobStore. Concurrent processes using the same resolved queue path
must fail without resetting running jobs. This lock protects supported receiver
startup, not arbitrary third-party SQLite tools. Preserve lock on disk and release
the descriptor after workers/database stop. First upgrade from the previous
unlocked version still requires stopping the old service.

Acceptance: legacy HTTP-only behavior; simultaneous HTTP and trusted HTTPS; legacy
writes continue authenticating with their existing token; distinct reviewer token
required for TLS POST and GET; no reviewer access on HTTP/proxy-spoof; TLS routes
isolated from legacy endpoints; exactly one startup/shutdown and worker pool;
invalid/missing TLS material, conflicting binds and second process ownership fail
without queue mutation; listener failure/SIGTERM/cancellation stop all tasks and
release sockets/queue lock; durable accepted/checkpointed jobs survive restart
without duplicate hooks. Exercise real sockets and TLS, not only mocked config.

This work prepares receiver transport. Production deployment and actual gateway
result correlation remain separate qualification items; no automatic reviewer
submission enablement follows from adding a listener. Keep the restored native HUD.
