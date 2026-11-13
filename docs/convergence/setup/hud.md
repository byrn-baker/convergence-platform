# NetClaw HUD with the Convergence views

NetClaw's Visual HUD (`ui/netclaw-visual`) has no plug-in point yet. Upstream spec 196
proposes one for dashboard views; until it ships, `setup/hud.py` builds the HUD from an
unmodified NetClaw checkout with Convergence's additions and points the two HUD units at
the result. The checkout itself is never changed, so `git pull` stays clean.

What the build adds (`integrations/netclaw/hud`):

| Directory | Contents |
|---|---|
| `overlay/` | New HUD files: the module loader, the Convergence module (Convergence tab, diary, security, VM security, host heartbeat, Proxmox observer, incident and JEV review), the retro-theme and scene-quality modules, local extensions (token footer, model picker, RAG URL and crawl ingest, SSH terminal), org-chart zones and the PWA files |
| `patched/` | Our versions of 23 upstream HUD files: the module hooks in `server.js` and `src/main.js`, the dashboard's Convergence button and view, chat model fixes, the Knowledge panel's URL ingest, org-chart drag and layout, `classic.html` tabs |
| `patched-base/` | The upstream version each patched file was made from |
| `repo-overlay/` | Files outside the HUD directory: `scripts/netclaw-apply-models.sh`, used by the model picker |

At build time each patched file is merged three ways onto the checkout's version of it
(`git merge-file`). Where upstream has not changed the file, ours is used as is. A conflict
stops the build and names the file: rebase the overlay rather than drop either side. CI
builds the HUD against a pinned upstream commit and runs its full test suite.

## Install

```bash
python3 setup/hud.py plan  --netclaw-dir ~/netclaw-production-candidate --ssh-terminal
python3 setup/hud.py apply --netclaw-dir ~/netclaw-production-candidate --ssh-terminal --restart
python3 setup/hud.py status --probe
```

- `plan` builds nothing and changes nothing.
- `apply` builds the tree under `~/.local/share/convergence/hud/<commit>-<overlay>/` (about
  200 MB with `node_modules`; a rebuild for the same commit and overlay is skipped). It then
  writes `zz-convergence-hud.conf` drop-ins for `netclaw-hud.service` and
  `netclaw-hud-ui.service` (systemd user units) that set `WorkingDirectory` and load
  `~/.config/convergence/hud.env`. With `--restart` it restarts both units and requires the
  API to list the Convergence module as configured; otherwise it removes its drop-ins again
  and restarts the old HUD.
- `hud.env` holds settings that used to come from the checkout's `.env`:
  `NETCLAW_TESTBED_FILE` (default `~/.openclaw/workspace/testbed/testbed.yaml`, the devices
  the HUD shows), `HUD_SSH_ENABLED` (`--ssh-terminal`), `NETCLAW_ENV_FILE` (the model
  picker's source of truth, `~/.openclaw/.env`) and any `--env KEY=VALUE`.
- The Convergence module reads `CONVERGENCE_API_URL` and its tokens from `~/.openclaw/.env`.
- A drop-in or env file with the same name that this tool did not write blocks `apply`.

To move to a newer NetClaw commit, update the checkout and run `apply --restart` again.

## Remove

```bash
python3 setup/hud.py remove --restart [--purge]
```

`remove` deletes the drop-ins and env file it wrote (a file edited since is kept and
reported), and `--purge` also deletes the build trees. The units then run from their own
`WorkingDirectory` again.

## Known differences from the fork HUD

- The footer's "Last turn" token figure stays empty: it needed a change inside upstream's
  `/api/chat` handler. Lifetime totals still show.
- Upstream's own Convergence view (retained flows through `convergence-read`) is replaced
  by Convergence's view in the dashboard. `CONVERGENCE_ENABLED` is not needed.
