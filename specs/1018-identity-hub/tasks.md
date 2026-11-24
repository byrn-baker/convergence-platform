# Tasks

Each live step needs owner approval: show the change, then apply it.

## 0. Interim, before any of this

- [x] I0: nftables table on the HUD VM allowing port 3000 only from approved
  workstations; persistent system unit; verified from an allowed and a blocked host.
  Live 2026-11-21 21:09 UTC: `/etc/netclaw/hud-firewall.nft` loaded by
  `netclaw-hud-firewall.service` (enabled), allowing 192.168.100.32 only. The IDS VM
  timed out on port 3000, the receiver on 8446 was unaffected, and the workstation's
  packets were accepted. Rollback: `sudo systemctl disable --now netclaw-hud-firewall`.

## 1. Identity provider

- [x] K1: Argo application for Keycloak and its PostgreSQL, ingress, certificate,
  backup CronJob; admin console not routed (port-forward only). Render and review the
  manifests before sync. Prepared on ops branch `identity-keycloak`; local smoke test of
  the pinned images passed (ready in about 35 s, issuer correct behind proxy headers,
  admin console on localhost). Deployed 2026-11-21 (owner approved): ops commits
  b1753a1, 388fb2f, eac135f, a4b83b0 and 71e63ec, manual sync, Synced/Healthy. pfSense
  override `sso` -> 192.168.13.201. The certificate is from Let's Encrypt, valid to
  2027-02-19. The issuer is correct over HTTPS, and `/admin`, `/health` and `/metrics`
  return 404. The backup produced a valid dump (202 table entries). The network policy
  blocks unlabelled pods. Automated sync is still off. Attempts 4/10:
  1. deploy and the first backup failed;
  2. backup waits for the database;
  3. `PGUSER` set, because uid 999 has no passwd entry;
  4. stale partial dumps removed.

  The claim-template drift fix rode with attempt 1.

  Independent review (Grok timed out; Claude reviewer): no critical defects. Three fixes
  were applied on 2026-11-21 at 21:49 UTC (ops 48f1ae8, owner approved), and a pod in
  another namespace was verified blocked:
  - a Keycloak network policy, because any pod could reach `/admin` past the ingress
    filter;
  - optional bootstrap-admin Secret references, because deleting the Secret would block
    a restart;
  - a restore procedure that avoids self-heal and the backup job, and runs in one
    transaction.

  For K2: enable brute-force detection on `master` too, consider returning 404 for
  `/realms/master` at the ingress, and test admin login through the port-forward before
  removing the bootstrap admin.
- [x] K2: Realm `home` as reviewed configuration: groups, required MFA, the two OIDC
  clients with exact redirect URIs. Owner enrols their own passkey or TOTP.
  Done 2026-11-21 (owner approved), ops 6005727; configuration as code in
  `gitops/home/identity/realm` (`configure.py` is idempotent):
  - realm `home` with brute-force protection, `sslRequired` all and a 14-character
    password policy;
  - flow `browser-mfa`: OTP or WebAuthn as the second factor, with OTP enrolment as a
    default required action;
  - groups `netclaw-owners` and `convergence-users`;
  - confidential PKCE clients `netclaw-hud` and `convergence-cases`, each with a `groups`
    claim in the ID token; the secrets exist in Keycloak only;
  - users `byrn` (both groups) and `byrn-admin` (master admin), both enrolled by the owner
    with a changed password and OTP;
  - client `ops-automation`, limited to managing realm `home`;
  - public `/realms/master` redirects to the `home` account page.

  A probe user was sent to OTP setup after the password, then deleted. Attempts 3/10: a
  mandatory second-factor subflow fails in Keycloak 26.8 for a user with no factor, so
  the stock conditional subflow plus the default required action is used. Residual risk:
  a user who deletes every factor signs in with a password until re-enrolled.

  Console fix (ops 08472fd): the public master redirect also blocked the admin console's
  own sign-in, so master's frontend URL is pinned to `http://localhost:8080`. Admin
  access is `kubectl -n identity port-forward svc/keycloak 8080:8080` on the HUD host
  plus an SSH tunnel. The bootstrap admin and its Secret are removed (ops f273c51,
  1590631); Keycloak restarted cleanly without them, and the one-time password files are
  shredded.
- [ ] K3: Restore drill: rebuild the realm from the Git export and database backup on a
  disposable instance.

## 2. Cases sign-in through the IdP (platform)

- [x] C1: Account identity links `(issuer, subject)` with an additive migration;
  admin-only linking.
  Table `reviewer_identity_links` in `grants.sql` (repeatable `CREATE TABLE IF NOT
  EXISTS`). `reviewer-accounts.js link|unlink USERNAME ISSUER SUBJECT` under the
  provisioning role; the runtime role can only read links. One identity links to one
  account, sign-in never creates a link, and unlinking ends that account's sessions.
- [x] C2: API OIDC grant exchange over mTLS with independent token checks.
  `POST /v1/grants/oidc` takes the ID token and the HUD's nonce. `src/reviewer/oidc.js`
  checks the RS256 signature against the issuer's JWKS (ten-minute cache, one refetch per
  30 seconds for an unknown key), the issuer, audience and authorized party, expiry and
  issue time with 60 seconds of skew, the nonce and the required group. Each token buys
  one grant (`reviewer_oidc_exchanges` keeps its digest until expiry). 401 for a bad or
  spent token, 403 for an identity with no enabled linked account, 503 when the keys
  cannot be fetched.
- [x] C3: Reviewer HUD relying party: login, callback, logout, server-side state, nonce
  and PKCE; password sign-in behind a break-glass setting.
  `/oidc/login` and `/oidc/callback` in the closed route table. State, nonce and verifier
  live in the HUD's memory for ten minutes. They are held in a dedicated sign-in attempt
  record with its own `SameSite=Lax` cookie instead of the pre-login session, so the
  session cookie stays `Strict`; the provider's redirect cannot carry a `Strict` cookie
  unless the provider is same-site. Sign-out returns the provider's end-session address.
  Password sign-in: `REVIEWER_PASSWORD_SIGN_IN=0` removes it on both tiers; when on beside
  an identity provider it needs `REVIEWER_PASSWORD_SIGN_IN_ADDRESSES`, and each attempt
  writes an audit line. The "Review this case" reference survives the round trip.
- [x] C4: Tests against a disposable Keycloak:
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

  Tests done 2026-11-21 on `feat/cases-oidc-signin` (42aefa5):
  - `tests/reviewer-oidc/run.sh`: real HUD, real service over mTLS, PostgreSQL 16 and
    Keycloak 26.8.1 at the production digest. 10 of 10 pass, covering every case above
    with tokens that Keycloak issued. The expired-token and rotation cases move the
    service's clock instead of waiting; the outage case pauses the container.
  - `ui/convergence-api/test-reviewer-oidc.cjs` (19 pass) and
    `ui/convergence-hud/test/reviewer-oidc.mjs` plus `reviewer-sso-page.mjs` (19 pass)
    cover forged tokens, algorithm confusion, skew boundaries and callback tampering.
  - Regression: reviewer API suites 123 pass (the permissions test now covers both roles
    on the new tables), HUD 93 pass, Helm 96 pass. One unrelated API test
    (`test-security-history.cjs`) fails only on the local fixture, which lacks the
    `POSTGRES_USER` variable that CI sets.

  Independent review, 2026-11-22: Grok Build timed out on four attempts, so nothing is
  claimed from it. A separate Claude reviewer (owner approved) read the whole change:
  no critical defects, two important and six minor. All were fixed on the branch:
  - the service now refuses to start on a schema without the two new tables, and only
    the OIDC path touches them, so an image rolled out before the migration leaves the
    previous replica serving instead of breaking password sign-in;
  - `unlink` locks the account before removing the link and its grants, and the grant
    exchange re-reads the link under its own account lock, so neither can pass the
    other (tested in both orders);
  - a callback with the wrong state no longer spends the attempt or counts against the
    sign-in limit, so another site cannot cancel a sign-in or use up the limit;
  - sign-in attempts are capped per address, and none is held while the provider is
    unreachable;
  - the attempt remembers the session that started it, so a repeat sign-in ends the old
    session even when the provider is cross-site;
  - the spent-token record is kept past the last moment the token could be accepted;
  - discovery is re-read with each key refresh;
  - a password attempt refused by address is audited.

  After the fixes: API 125 pass, HUD 93 pass, Keycloak qualification 10 of 10, Helm 96.
  Left as documented limits: the break-glass address list needs the HUD to see real
  client addresses (true for the production LoadBalancer with
  `externalTrafficPolicy: Local`); the real-Keycloak suite cannot produce a token with
  a foreign issuer under the same key, so that comparison is covered with forged tokens.
- [x] C5: Release; live cutover with the `byrn` link; one attributed disposition on the
  synthetic case; password sign-in off.
  Done 2026-11-22 04:37–04:53 UTC, owner approved. Release v0.1.16 (6c21b18, run
  44985276815); images `convergence-api@sha256:13c4d6f8…3f7e36` and
  `convergence-reviewer-hud@sha256:85fa50dc…3ff81f`.
  - Database: dump taken first; `reviewer-service.js migrate` from the release image
    exited 0; `permissions.sql` (sha256 478449d3…) applied in one transaction, 15 grants.
  - `byrn` linked to issuer `https://sso.internal.byrnbaker.me/realms/home` by subject,
    through the restricted provisioning role.
  - Ops 2fe6522: chart pinned to 6c21b18, OIDC values on, password sign-in limited to
    the owner workstation. Argo CD Synced/Healthy; `/oidc/login` returned 303 to the
    home realm with PKCE.
  - The owner signed in through Keycloak and recorded disposition operation
    800790f3-faf8-4698-895d-f0a33773b3fd on synthetic case
    89edb5f0-cdfa-4b32-b699-dec36242f095 at 04:46:50 UTC, attributed to account `byrn`.
  - Ops 704242c: password sign-in off. `POST /review/sign-in` with the site's Origin
    returns 404, and `/review/session` reports `signIn: {oidc: true, password: false}`.
  - Not exercised: a successful break-glass password sign-in from the allowed address.
    One attempt from it was rejected for its credentials at 04:42:06 UTC, during the
    rollout.

## 3. HUD front door

- [ ] F1: `HUD_PUBLIC_ORIGIN` as a platform overlay patch to the managed HUD build (never
  the fork), loopback only, with tests;
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
