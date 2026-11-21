# Design

```
browser ──HTTPS──> hud.internal.byrnbaker.me  (oauth2-proxy on the HUD VM)
                     │ OIDC, group netclaw-owners
                     └──> 127.0.0.1:3000  NetClaw HUD (no network listener)
browser ──HTTPS──> cases.internal.byrnbaker.me (reviewer HUD, OIDC relying party)
                     └──mTLS──> Convergence API: verifies ID token, issues grant
both ───────OIDC──> sso.internal.byrnbaker.me  Keycloak (k3s)  ──> AD/LDAP (later)
```

## Identity provider

- **Keycloak** on k3s, namespace `identity`, managed by Argo CD from the operations
  repository. Exact image version pinned. It supports OIDC, SAML brokering, LDAP and
  Active Directory federation, groups, WebAuthn and TOTP. Authentik is the alternative;
  Keycloak is preferred because it is the broker named in upstream R16.
- Dedicated PostgreSQL StatefulSet, following the cluster's per-application pattern, with
  a scheduled `pg_dump` backup. The realm configuration, without secrets, is exported to
  Git so it can be reviewed and rebuilt.
- Ingress `sso.internal.byrnbaker.me` through ingress-nginx, certificate from the existing
  `letsencrypt-prod` issuer (Cloudflare DNS-01).
- The admin console is reachable only from the owner workstation.
- Realm `home`. Groups:
  - `netclaw-owners`: may pass the HUD front door;
  - `convergence-users`: may sign in to Cases.

  Group membership only admits a person. Convergence still owns roles and sites.
- MFA is required: WebAuthn (a security key or passkey) preferred, TOTP accepted.
- Directory: none exists (owner, 2026-11-21), so Keycloak holds the accounts. No federation at first. Adding AD or LDAP later is a realm change: read-only
  federation over LDAPS, a dedicated bind account in a Kubernetes Secret, and a group
  mapper.
- Clients: `netclaw-hud` and `convergence-cases`, both confidential, authorization code
  flow with PKCE (S256), exact redirect URIs, client secrets in Kubernetes Secrets or the
  host's private environment file, never in Git.

## Cases site (platform code)

- **Reviewer HUD** becomes an OIDC relying party. It adds the login redirect, `/oidc/callback`
  and logout to its closed route table, and keeps state, nonce and the PKCE verifier
  server-side in the pre-login session. The session cookie, CSRF proof and 30-minute
  grant are unchanged.
- **Convergence API** gets a new grant exchange over the existing mTLS channel: the HUD
  passes the ID token and the nonce it issued. The API checks the token itself (issuer,
  audience, expiry with bounded clock skew, nonce, an allowed signature algorithm, keys
  from the IdP's JWKS with caching and rotation) and does not trust the HUD's reading of
  it.
- The identity key is the issuer plus subject, never the email address. A new table links
  `(issuer, subject)` to an existing Convergence account. Linking is an explicit admin
  action, so the existing `byrn` account and all its history keep their identity.
- Logout ends the local session and grant, then redirects to the IdP's end-session
  endpoint.
- Password sign-in stays in the code as break-glass behind a setting. It is turned off
  after cutover and, when on, is accepted only from the owner workstation and is audited.

## HUD front door (HUD VM)

- **oauth2-proxy** runs as a system service under its own account. It listens on
  192.168.3.252:443 for `hud.internal.byrnbaker.me`, requires group `netclaw-owners`, and
  proxies to `http://127.0.0.1:3000`, WebSockets included. The session cookie is Secure,
  HttpOnly and SameSite=Lax (the OIDC callback needs Lax).
- **NetClaw HUD** binds to loopback (`HUD_HOST=127.0.0.1`). It needs one fork-local
  setting, `HUD_PUBLIC_ORIGIN`, so its local-access check accepts that origin's Host and
  Origin headers, but only on loopback connections. This is the small seam that the
  upstream proposal generalizes.
- Convergence local-owner mode stays off. The gateway is a loopback hop, and the
  platform's owner check already rejects requests carrying forwarded headers; a test
  proves the gateway cannot unlock it.
- **Convergence in the native HUD** follows option 1 of
  [native-hud-plan.md](../1015-embedded-hud-sign-in/native-hud-plan.md): read-only views
  through the viewer key, and actions through "Review this case". With SSO, that link
  opens Cases already signed in. The admin key is then removed.
- **Certificate.** The certificate is issued by cert-manager in k3s and installed on the
  host by the existing renewal timer (`convergence-reviewer-tls`, spec 1016), which
  already handles key/certificate checks and reload. The alternative is lego with DNS-01
  on the host.
- **DNS.** Internal names are host overrides in the pfSense DNS Resolver, created through
  its REST API (v2) with the existing credentials: `sso` points to the k3s ingress
  (192.168.13.201) and `hud` to the HUD VM (192.168.3.252). Each record is added at its
  cutover step, with approval.
- **Network.** Ports 3000 and 3001 have no network listener. The interim nftables table
  restricting port 3000 is then removed. Port 443 is open to the LAN, and the front door
  authenticates every request.

## Failure and rollback

- **IdP down:** the HUD front door and Cases sign-in fail closed. Existing sessions run
  until they expire. Break-glass is the Keycloak admin account plus Convergence password
  sign-in from the owner workstation.
- **Rollback, front door:** stop oauth2-proxy, set `HUD_HOST` back to 192.168.3.252, and
  re-apply the interim firewall.
- **Rollback, Cases:** turn password sign-in back on and the OIDC setting off. The linked
  identities stay, and accounts, cases and audit records are untouched.
- **Rollback, IdP:** remove the Argo application. Its database backup is kept.
