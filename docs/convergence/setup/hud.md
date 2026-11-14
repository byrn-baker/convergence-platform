# Stage the NetClaw HUD

NetClaw's **Convergence** destination serves retained evidence. **Site operations** opens the Convergence workspace in the dashboard. In Classic, use the **Convergence** tab. Both entry points load the same views from `ui/convergence-hud/modules/convergence`. The Classic device detail opens the selected device in NetClaw's native Canvas SSH terminal. Connecting still requires a separate operator action and the native host-key policy.

## Access

On a network HUD, Site operations opens the deployment's dedicated HTTPS reviewer. A reviewer signs in there using their own Convergence account. A case link carries the site and case ID in its fragment. After sign-in, the reviewer checks the user's site grants and lets them open the selected case. Case changes and audit records use that person's identity.

Provision the following settings in a service-manager environment file outside the HUD-editable runtime configuration. Use a mode-0600 file readable by the service account. Keep it outside Git, command arguments and shell history. The reviewer origin is the exact HTTPS origin with a DNS hostname.

| Service setting | Purpose |
|---|---|
| `CONVERGENCE_REVIEWER_ORIGIN` | Dedicated reviewer origin for this deployment |
| `CONVERGENCE_HUD_LOCAL_OWNER` | Set to `1` only for a HUD served exclusively on loopback |
| `CONVERGENCE_API_URL` | Convergence API origin over HTTPS, or HTTP on a literal loopback address |
| `CONVERGENCE_SITE` | Single site assigned to this local installation |
| `CONVERGENCE_HUD_READ_TOKEN` | Separate API key of at least 32 characters assigned the viewer role for that site |

LAN installations need only the reviewer origin for this feature. Do not enable local owner mode behind a proxy or tunnel that presents network clients as localhost. Local telemetry reads require the browser's installation-specific NetClaw owner cookie. The server checks the binding before dispatch and before returning backend data. Site inventory shows unavailable until its backend can prove site scope. Model readouts, token summaries and fork RAG intake require a local owner. RAG intake uses upstream URL preview and confirmed first-level linked pages. NetClaw Chat retains per-conversation model selection. This build omits the fork’s global preset writer, which could partially change runtime settings.

## Build and inspect

Run from the Convergence checkout. Substitute the intended clean NetClaw checkout and the ports and host used by its services.

```sh
python3 setup/hud.py plan --netclaw-dir ~/netclaw-production-candidate
python3 setup/hud.py build --netclaw-dir ~/netclaw-production-candidate
```

`plan` reports conflicts and the proposed service files. `build` creates a reusable artifact under `~/.local/share/convergence/hud`. The installer uses the committed NetClaw tree, the platform's view package, compact host patches and the adapters under `integrations/netclaw/hud`. If upstream and a host patch have overlapping edits that Git cannot reconcile, the build reports the affected file. NetClaw's accepted upstream specifications cover extension registration and owner recognition. This staging adapter supplies their integration until an upstream implementation can replace it.

## Activate and recover

After approving the plan, activate during the deployment window.

```sh
python3 setup/hud.py apply --netclaw-dir ~/netclaw-production-candidate --restart
python3 setup/hud.py status --probe
```

For a nondefault service, pass the actual `--api-port`, `--ui-host` and `--ui-port` when probing or activating. These arguments select the endpoints to verify. Preserve the services' deployment environment, including the bind address. Activation manages a drop-in for each user service and its generated environment file. It checks both running working directories, the API module index and the served dashboard and Classic assets. If a restart or probe fails, the installer restores the prior managed files and service state. A subsequent activation recovers interrupted changes.

```sh
python3 setup/hud.py remove --restart
```

Removal reports locally edited files and preserves their dependencies. Add `--purge` to remove eligible inactive managed builds.

## Qualify a build

The browser test starts the assembled server with a disposable installation and synthetic site data. It exercises network and loopback access, actual session revocation, the case reference and native SSH device selection in Chromium.

```sh
tree=$(python3 setup/hud.py build --netclaw-dir ~/netclaw-production-candidate)
(cd tests/hud-browser && npm ci && npx playwright install chromium && node qualify.mjs "$tree")
```
