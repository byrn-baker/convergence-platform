# HTTPS investigation review

This dedicated Convergence HUD entry point lets two people review a case with their own accounts. The page displays the case, security assessment, current disposition and review history. Operators can record a disposition and reconcile a submission by its receipt. The history records the reviewer’s stable ID and display name at the time of the action. Opening a case requires its site and UUID. Assignment, reinvestigation and JEV dispatch have separate workflows.

## Start the HUD

Use Node 24 and a private Convergence reviewer API with PostgreSQL, individually provisioned accounts and an enrolled installation certificate. The runtime uses only built-in Node modules. The [private service](https://github.com/byrn-baker/convergence-platform/pull/10) documents certificate enrollment, migration and provisioning. Configuration uses environment variables and mounted certificate files.

| Setting | Purpose |
| --- | --- |
| REVIEWER_HUD_ORIGIN | Exact HTTPS origin that reviewers enter, e.g. https://review.example.org. Use a dedicated hostname with its own origin and site data. |
| REVIEWER_HUD_HOST, REVIEWER_HUD_PORT | Concrete bind address and port. Defaults to 127.0.0.1 and 3444. Set the external port in the origin if appropriate. |
| REVIEWER_HUD_CERT, REVIEWER_HUD_KEY | Paths to the browser-facing certificate chain and private key. The certificate must match the configured reviewer hostname and be trusted on the workstation. |
| REVIEWER_SERVICE_ORIGIN | Exact private HTTPS origin of the reviewer API. |
| REVIEWER_SERVICE_CA | Path to the CA that issued the private service’s server certificate. |
| REVIEWER_INSTALLATION_CERT, REVIEWER_INSTALLATION_KEY | Enrolled installation’s client certificate and key for mutual TLS. Keep these accessible only to the HUD process. |

From the convergence-hud package directory, run:

```sh
npm run reviewer
```

Route only the dedicated hostname to this listener. Direct TLS or layer-4 TLS passthrough preserves end-to-end HTTPS and the original Host and Origin. A trusted reverse proxy that terminates and re-encrypts traffic needs independent ingress qualification. The application never uses forwarded identity headers. Keep the native owner HUD, machine credentials, private API port and WebSocket facilities off this route. A copied review URL cannot supply a service grant.

Each sign-in rotates a host-only secure cookie and binds it to one server-held grant. Serve one HUD process per reviewer origin, or route the same session to the same HUD replica. The private API supports multiple replicas. Restarting the HUD ends its in-memory associations. The private API enforces a 30-minute maximum and a 10-minute idle limit. A case read or background poll does not renew the idle limit. An acknowledged write can extend it up to the absolute deadline.

## Qualify and roll back

Check certificate trust from a second workstation, run viewer and operator accounts across two test sites, confirm the saved disposition and original reviewer in PostgreSQL, sign out one user and check the other. Exercise hostile origins, direct ingress bypass and rejection of native HUD paths. Verify the readable review still belongs to the authorized site after a refresh. If a submission is interrupted, check its receipt before attempting it again.

To roll back, remove the reviewer ingress and stop the reviewer HUD. Remove enrollment of that installation on every API replica and restart the private service for the rotation to take effect. Keep the database and immutable history. Signing in through the existing standalone dashboard and the generic NetClaw view registration have separate entry points. Native host integration is proposed in [NetClaw #357](https://github.com/automateyournetwork/netclaw/pull/357).

For containers, build using reviewer/Dockerfile with the reviewer directory as its context. The image runs as the unprivileged node user. Mount certificate files read-only and make the key readable by that service user. The release image is built alongside the platform images on a coordinated version tag. Pin the published digest in the deployment.
