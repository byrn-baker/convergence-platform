# Identity hub: single sign-on for the HUD and Cases

**Status** New feature, exempted from the spec 1017 feature freeze by the owner on
2026-11-21 (AGENTS.md; spec 1017 A02). The freeze was not raised before the K1 Keycloak
deployment, which the owner had approved. Each live step still needs owner approval.
Stage 1 of
the identity recommendation; stage 2 is the upstream proposal in
[upstream-proposal.md](upstream-proposal.md).

## Problem

NetClaw has no sign-in for LAN access. Its network HUD treats the interface as trusted, so
anyone who reaches port 3000 gets the full HUD: the terminal, chat, model settings and, in
the production fork, an admin path to Convergence. Convergence has its own accounts with
local passwords. There is no single sign-on, no MFA and no way to use a directory.
Upstream NetClaw plans multi-user identity and SSO (issue #310, briefs R15–R17), but none
of that has started.

## Required behavior

1. One identity provider (IdP) serves both NetClaw and Convergence. A person signs in once
   and reaches both. MFA is required.
2. The ordinary NetClaw HUD is reachable from the LAN only through an authenticated HTTPS
   front door. Its own listener is never exposed to the network. Only members of an
   owner group get through.
3. The Cases site signs people in through the IdP. Convergence still decides each
   person's role and sites, and every action keeps that person's name. A user disabled in
   the IdP cannot start a new session; existing grants end within their 30-minute limit.
4. A directory (Active Directory or LDAP) can back the IdP later without changes to
   NetClaw or Convergence. Until one exists, the IdP holds the accounts.
5. An audited break-glass path exists if the IdP is down: the IdP's own admin account,
   and Convergence password sign-in restricted to the owner workstation. Both are off for
   normal use.
6. Phone access works through the same front door.

## Out of scope

- Multi-user NetClaw. Behind the front door NetClaw still has one owner, so everyone
  admitted shares its sessions, terminals and chat. The owner group therefore has one
  member until upstream R15 isolation exists.
- SAML and other protocols directly in NetClaw or Convergence. Both speak OIDC only; the
  IdP brokers anything else.
