# Draft upstream proposal: trusted-gateway sign-in for the NetClaw HUD

**Status** Draft for owner review before it is opened on automateyournetwork/netclaw.
Intended as the first slice of roadmap brief R15 (issue #310), using R16's OIDC checks.

## Summary

Let a NetClaw HUD on a network sit behind an identity-aware gateway, such as
oauth2-proxy with Keycloak, Entra ID or another OIDC provider. NetClaw verifies who the
person is from a token the identity provider signed. It never trusts a forwarded header.
That gives the HUD a server-side principal, which R15's authenticator interface needs
and which today's "trusted interface" remote mode lacks.

## Why

- The network HUD has no authentication. Remote mode is an opt-in trusted interface, so
  anyone who reaches the port gets the terminal, chat and model settings.
- Spec 197 (#357) covers loopback owners and a dedicated HTTPS reviewer. It leaves the
  ordinary network HUD without identity, so downstream views cannot attribute actions to
  a person there.
- R15 asks for a pluggable authenticator, and R16 for OIDC providers configured as data.
  A gateway provider is the smallest useful slice: it covers SSO, MFA and directories
  through the identity provider, with no passwords stored in NetClaw.

## Proposed behavior

1. **Configuration.** An administrator sets the HUD's public HTTPS origin, the trusted
   hop and the OIDC issuer and audience. Until that is done, nothing changes.
   - The trusted hop is either loopback or an mTLS client certificate.
   - There is no default trusted hop and no trust in headers.
2. **Verification.** The gateway forwards the provider-signed token. NetClaw checks it
   on every HTTP request and WebSocket upgrade:
   - signature against cached provider keys, with rotation;
   - issuer, audience, expiry with bounded skew, and an allowed algorithm.

   It then admits the principal `(issuer, subject)` only if an allow rule matches
   (group, tenant or domain). Requests from any other hop, or with a missing or invalid
   token, are refused.
3. **Single owner.** Until R15 isolation exists, exactly one configured principal maps to
   the owner. Any other principal is refused rather than sharing the owner's sessions,
   terminals or chat.
4. **Spec 197 adapters.** They receive the verified principal instead of a loopback-only
   owner handle. A downstream application can then exchange it for its own grant, so
   actions are attributed to the person.
5. **Lifecycle.** Logout and expiry at the gateway end NetClaw access within the token
   lifetime. The existing loopback owner mode is unchanged.

## Acceptance

Tests cover the following:
- tampered, expired, wrong-issuer and wrong-audience tokens;
- a token presented on an untrusted hop;
- spoofed forwarded headers;
- key rotation;
- a WebSocket without a valid token;
- a second principal refused;
- loopback owner mode unchanged when the feature is off.

A reference deployment uses oauth2-proxy with Keycloak. The VS Code and Mobile impact
dispositions follow R15/R16 (G12/G13).
