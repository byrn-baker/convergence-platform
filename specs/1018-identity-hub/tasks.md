# Tasks

Each live step needs owner approval: show the change, then apply it.

## 0. Interim, before any of this

- [ ] I0: nftables table on the HUD VM allowing port 3000 only from approved
  workstations; persistent system unit; verified from an allowed and a blocked host.

## 1. Identity provider

- [ ] K1: Argo application for Keycloak and its PostgreSQL, ingress, certificate,
  backup CronJob; admin console limited to the owner workstation. Render and review the
  manifests before sync.
- [ ] K2: Realm `home` as reviewed configuration: groups, required MFA, the two OIDC
  clients with exact redirect URIs. Owner enrols their own passkey or TOTP.
- [ ] K3: Restore drill: rebuild the realm from the Git export and database backup on a
  disposable instance.

## 2. Cases sign-in through the IdP (platform)

- [ ] C1: Account identity links `(issuer, subject)` with an additive migration;
  admin-only linking.
- [ ] C2: API OIDC grant exchange over mTLS with independent token checks.
- [ ] C3: Reviewer HUD relying party: login, callback, logout, server-side state, nonce
  and PKCE; password sign-in behind a break-glass setting.
- [ ] C4: Tests against a disposable Keycloak:
  - tampered state, nonce or PKCE;
  - replayed code;
  - wrong issuer or audience;
  - expired token;
  - key rotation;
  - IdP outage;
  - an unlinked subject;
  - a disabled user;
  - two users in independent sessions.

  Then an independent review.
- [ ] C5: Release; live cutover with the `byrn` link; one attributed disposition on the
  synthetic case; password sign-in off.

## 3. HUD front door

- [ ] F1: `HUD_PUBLIC_ORIGIN` in the fork's local-access check, loopback only, with tests;
  a test that gateway traffic cannot unlock Convergence local-owner mode.
- [ ] F2: oauth2-proxy unit and configuration, certificate installed by the renewal
  timer, DNS record for `hud.internal.byrnbaker.me`.
- [ ] F3: Side-by-side qualification on spare ports. Classic and the dashboard, VM
  Security, heartbeat, terminal and chat work through the gateway, on desktop and phone.
  Direct access to 3000 and 3001 from the LAN fails.
- [ ] F4: Cutover: HUD to loopback, front door live, interim firewall removed.
- [ ] F5: Native HUD Convergence read adapter (native-hud-plan option 1); after a 24-hour
  soak, remove the `hud` admin key.

## 4. Later

- [ ] D1: AD/LDAP federation when a directory exists.
- [ ] U1: Upstream proposal for the NetClaw trusted-gateway authenticator (R15 seam).
