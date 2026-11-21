# Native HUD sign-in plan

**Status** Option 1 chosen by the owner, 2026-11-21, together with the identity hub in
[spec 1018](../1018-identity-hub/spec.md). With single sign-on, "Review this case" opens
the Cases site already signed in, and the HUD itself sits behind an authenticated front
door. No code or live change has been made yet.

## Goal

From the owner's workstation (192.168.100.32), the ordinary NetClaw HUD keeps live
Convergence telemetry in Classic and the dashboard, including VM Security and Host
Heartbeat. Every case action is made by a person signed in to their own Convergence
account, never by a shared machine key. This closes the two open
[tasks.md](tasks.md) items for the native HUD: qualifying its adapter and repeating
disposition attribution through it.

## Current state, checked 2026-11-21

**The production HUD has an open admin path to Convergence.**

- The HUD runs from the NetClaw fork at `/home/ubuntu/netclaw/ui/netclaw-visual`:
  - `netclaw-hud` serves the API on 127.0.0.1:3001;
  - `netclaw-hud-ui` runs `vite preview` on plain HTTP at 192.168.3.252:3000.
- The host firewall is inactive. NetClaw's remote mode is a trusted-interface opt-in,
  not authentication. Any client that can reach port 3000 gets the whole HUD.
- The fork's Convergence module (`modules/convergence/server.js`) forwards every
  method and path under `/api/home/` to the Convergence API with the `hud` API key.
  That key has the **admin** role for site `home`.
- So a LAN client can make these changes without signing in:
  - security dispositions;
  - ISP reports;
  - JEV reviews;
  - reinvestigations;
  - PATCH edits of events.

  Convergence records every one of them as "HUD", not as a person. A read-only check
  through port 3000 returned 200. No write was attempted.

**Platform main already has the replacement pieces.**

- `ui/convergence-hud` views: these are the fork's views plus a read-only mode. In that
  mode each write control becomes a "Review this case" link to the Cases site
  (`ReviewerLink.js`). VM Security, Host Heartbeat and Proxmox views are identical to
  the fork's.
- `integrations/netclaw/hud`, the read adapter:
  - finite GET-only operations;
  - a viewer-role key (`CONVERGENCE_HUD_READ_TOKEN`) for one site;
  - response projection with site and secret-field checks;
  - no write path.
- `setup/hud.py` builds, activates and removes the HUD. It probes the result and
  restores the previous state automatically.

**Why the earlier cutover lost telemetry.**

- The adapter serves reads only to a verified local owner. That requires a
  loopback-only HUD (`CONVERGENCE_HUD_LOCAL_OWNER=1`). The production HUD is used over
  the LAN, so every read was denied and the views showed "unavailable".
- Device inventory is deliberately unavailable in that adapter, because the legacy
  `/api/devices` is not site-scoped.
- The build also starts from a clean upstream checkout (`~/netclaw-production-candidate`
  at upstream `a5e7c3a3`) plus the platform overlay, not from the fork. The fork's
  `ui/netclaw-visual` differs from upstream in 161 files.

**Upstream.** NetClaw spec 197 (PR #357) was merged on 2026-11-13 as a design only.
Upstream main is now `9104323d`. None of its four commits since `a5e7c3a3` touch the
HUD, so none of the host implementation exists yet. The design limits local-owner mode
to loopback. It also states that the ordinary network HUD cannot establish a reviewer
session. Sign-in inside the LAN HUD is therefore outside the accepted design.

## Options

### 1. Network read profile, with sign-in on the Cases site (recommended)

Add an explicit opt-in network-read mode to the platform adapter. On a HUD with remote
access enabled, it serves the same finite, site-scoped reads with the viewer key,
without needing an owner cookie.

- The admin proxy and the admin key are removed.
- Each write control opens the case on https://cases.internal.byrnbaker.me. The person
  signs in there, and Convergence records their own name for the action.
- A host firewall rule limits who can reach the HUD to the devices you choose.

Benefits:
- Telemetry stays intact, and every change is attributed to a named person.
- No anonymous admin access remains on the LAN.
- No upstream dependency: it reuses adapter code and views that are already qualified.

Costs:
- Any device allowed to reach the HUD can read site telemetry without signing in. That
  is the case today, but access becomes read-only at viewer scope.
- Actions open a second tab, and sign-in happens there, not inside the HUD.

### 2. Loopback-only HUD over an SSH tunnel

Bind the HUD to 127.0.0.1 and open it through `ssh -L 3000:127.0.0.1:3000`. Local-owner
mode then works exactly as built and qualified: reads require the NetClaw owner cookie,
and nothing is exposed on the LAN.

- Writes still go to the Cases site, as in option 1.
- It changes how you open the HUD every time, and it ends phone access.

### 3. Sign-in inside the network HUD (defer)

This needs four things, in order:
1. HTTPS on the ordinary HUD.
2. An upstream amendment to spec 197 that allows an authenticated network session.
3. Upstream implementation of the host extension: spec 197 tasks 3–8 are open.
4. A per-user Convergence grant held by the HUD.

It would let you sign in and act without leaving the HUD. But it depends on maintainer
agreement and adds the most new attack surface. Revisit it only if the Cases-site
hand-off proves inconvenient in practice.

## Option 1 work

### A. Platform, isolated, no live change

1. **Network read mode.**
   - Add the setting `CONVERGENCE_HUD_NETWORK_READS=1`. It is accepted only when the
     HUD listens on a concrete non-loopback address and the host's local-access check
     passes: exact Host and Origin authority, and no cross-site fetch metadata.
   - The access decision stays in `owner-access.js`, as one function that returns
     either the verified local owner or network-read access. `read-operations.js`
     stays unchanged.
   - The loopback-owner path stays unchanged.
   - The status endpoint reports which mode is active.
2. **Site-scoped device inventory.** Add a site-scoped devices read for the API-key
   path, reusing the shared telemetry service extracted in H2. Without it the native
   Devices view stays unavailable, though the Cases site still shows devices.
3. **Write controls.** Confirm that read-only views turn every write into a Cases-site
   link: disposition, ISP report, JEV review, reinvestigation and feedback.
4. **Tests.**
   - Mode selection:
     - the loopback owner path is unchanged;
     - network mode needs the flag, a remote bind and the local-access check;
     - foreign Host or Origin, cross-site fetches, forwarded headers, POST, PATCH,
       HEAD and request bodies are all denied.
   - Projection for every resource.
   - Extend the browser qualification (`tests/hud-browser/qualify.mjs`) with a
     network-read run:
     - Classic and the dashboard render health, Wi-Fi, security, VM Security,
       heartbeat, flows, diary and alerts;
     - write controls are links;
     - the browser makes no non-GET `/api/home` requests.
5. Independent review, then merge and a platform release.

### B. Production preparation, no live change

1. **Deployment method: the platform-managed HUD build, never an edit to the fork.**
   The fork in `~/netclaw` is being retired and must not be modified (operations home
   rules; Stage 9/10).
   - Run `setup/hud.py plan` against the production candidate
     (`~/netclaw-production-candidate`). It is read-only.
   - Compare the built HUD's routes, modules and dashboard panels with the running fork.
   - A missing feature becomes a platform overlay or upstream task, not a fork patch.
   - Record the comparison.
2. **Side-by-side run.** Start the candidate on loopback at other ports with a
   temporary viewer key. Compare the 11 telemetry resources used for H5 against the
   running HUD.
3. **Write the cutover diff.** It covers three changes:
   - a new viewer key `hud-read` (site `home`) in the `convergence-secrets` API keys;
   - the HUD service environment file, mode 0600;
   - the firewall rule (step C2).

### C. Cutover, after owner approval

1. Add the `hud-read` key, then activate the HUD with `setup/hud.py apply --restart` and
   `status --probe`, or with the module swap.
2. Add a host firewall (nftables) rule on the HUD VM:
   - port 3000 is reachable only from the devices you approve, plus loopback;
   - SSH is unchanged.

   This also stops other LAN hosts from reaching the HUD's terminal, chat and model
   settings.
3. Verify from the workstation:
   - all 11 telemetry resources are live in Classic and the dashboard, including VM
     Security and Host Heartbeat;
   - "Review this case" opens the case on the Cases site, and a disposition there
     records your name;
   - a POST to `:3000/api/home/...` returns 405;
   - a LAN host that isn't allowed cannot connect.
4. After a 24-hour soak, remove the `hud` admin key from `convergence-secrets`. Keep its
   value in its existing owner-private file until then.
5. Close the two open spec 1015 tasks with the evidence. Update
   [follow-up.md](follow-up.md) and the operations handoff.

### Rollback

- **Before step C4:** run `setup/hud.py remove --restart`. This puts the current fork
  units back, with the admin key still configured.
- **After step C4:** do the same, and also re-add the `hud` key from its owner-private
  file.
- **Firewall:** delete the nftables table.
- Case and audit records are never touched.

## Decisions for the owner

1. Which option: 1, 2 or 3?
2. Which devices may reach the HUD: the workstation only, or a phone or others too?
3. Is it acceptable that site telemetry is readable without sign-in from those devices?
4. Should site-scoped device inventory be built for the native HUD (step A2), or left
   to the Cases site?
