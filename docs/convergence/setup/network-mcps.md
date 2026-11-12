# pfSense and UniFi MCP servers

Convergence investigations read the edge firewall and the Wi-Fi network through two
community MCP servers on the NetClaw host. `setup/network_mcps.py` installs them at
pinned commits and registers them in OpenClaw, under the names the
[investigator member spec](investigator.md) and the alert skills use:

| OpenClaw server | Source (pinned) | Runs |
|---|---|---|
| `pfsense-mcp` | [gensecaihq/pfsense-mcp-server](https://github.com/gensecaihq/pfsense-mcp-server) v1.1.0 | read-only: `MCP_READ_ONLY=true` leaves 130 read tools of 331; `MCP_ENABLE_LOG_FILES=false` also drops `get_log_file`, which reads raw `/var/log` files through the firewall's command endpoint (the API log tools stay) |
| `unifi-network` | [byrn-baker/unifi-mcp](https://github.com/byrn-baker/unifi-mcp) `integration-devices` (upstream v0.9.0 plus [chris2ao/unifi-mcp#9](https://github.com/chris2ao/unifi-mcp/pull/9)) | the Integration API device reads (`integration_*`) registered at startup |

The UniFi pin moves to an upstream release once #9 is merged. It adds the
`integration_*` device tools, which work on UniFi OS Server installs where the legacy
controller paths hang, and `UNIFI_PRELOAD_GROUPS`. The investigator may call those
reads but not `load_network_tools`, so the helper preloads `network:integration`.
UniFi has no read-only mode: its configuration-changing tools need a preview and
`confirm=True`, and each agent's tool allowlist decides what it may call.

## Credentials

Keep them in `~/.openclaw/.env`. The helper writes only `${VAR}` references into
`openclaw.json` and refuses to continue while a required variable is missing.

| Variable | Needed |
|---|---|
| `PFSENSE_URL` | yes, e.g. `https://192.0.2.1` |
| `PFSENSE_VERSION` | yes: one v1.1.0 accepts (`CE_2_8_1`, `CE_2_9_0`, `PLUS_25_11_1`, `PLUS_26_03`, `PLUS_26_03_1`, `PLUS_26_07`; legacy `CE_2_8_0`, `PLUS_24_11`, `PLUS_25_11`, `CE_26_03`). `--pfsense-version` writes it |
| `PFSENSE_AUTH_METHOD` | `api_key` (default) needs `PFSENSE_API_KEY`; `basic` needs `PFSENSE_USERNAME` and `PFSENSE_PASSWORD` |
| `PFSENSE_VERIFY_SSL`, `PFSENSE_CA_FILE` | optional; prefer the firewall's CA file to turning verification off |
| `UNIFI_HOST`, `UNIFI_API_KEY` | yes (a local-console Integration API key) |
| `UNIFI_SITE`, `UNIFI_VERIFY_SSL`, `UNIFI_TOOL_GROUPS`, `UNIFI_PRELOAD_GROUPS` | optional |

v1.1.0 refuses a `PFSENSE_VERSION` it does not list. The version only selects a
name: set the one for your firewall (System > Information), or the nearest listed
release on the same branch.

## Install

```bash
python3 setup/network_mcps.py plan
python3 setup/network_mcps.py apply --replace-existing --restart
python3 setup/network_mcps.py status --probe
```

- `plan` changes nothing and shows the exact config patch.
- `apply` clones each server into `~/.local/share/convergence/mcp/<server>/source`,
  verifies the checkout is the pinned commit, and builds a virtualenv beside it
  (pfSense needs Python 3.11+, UniFi 3.12+; `--python` picks the interpreter). It then
  starts each server once over MCP stdio with placeholder credentials and requires a
  tool list. Listing tools contacts no device. Only then does it back up `openclaw.json`
  and `.env` to `~/.openclaw/backups/`, and write the entries with `openclaw config
  patch` after a dry run.
- An entry with the same name that this tool did not create, such as one from the
  NetClaw fork, is replaced only with `--replace-existing`. The replacement is exact:
  keys of the old entry are removed, not merged.
- `--server pfsense` or `--server unifi` limits a run to one server.
- `--pfsense-read-write` sets `MCP_READ_ONLY=false` (all 331 tools). Investigations do not
  need it; keep it when an operator-confirmed workflow writes to the firewall, such as
  adding an address to a block alias. Each agent's tool allowlist still decides who may
  call the write tools. Run `apply` with the same flags each time: without it, a re-apply
  switches the server back to read-only.
- `status --probe` probes the entry as registered, so its tool count shows the mode.
- `--restart` restarts `openclaw-gateway.service` so the gateway loads the servers.

## Remove

```bash
python3 setup/network_mcps.py remove --restart
```

`remove` restores the entries `apply` replaced exactly as they were, or deletes the
ones it added, along with a `PFSENSE_VERSION` it changed and the directories it
created. An entry changed since install is left as it is and reported.

## Members

A member provisioned from the Border template (`in2n-member-home.py`, including the
[investigator](investigator.md)) copies these entries with their `${VAR}` references
and gets the referenced variables in its own env slice. Existing member homes keep
their old entries until they are re-provisioned.
