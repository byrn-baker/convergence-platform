# NetClaw investigations

Convergence's alert receiver opens a case for every alert. With this step, NetClaw
investigates the cases the investigation policy admits and records its evidence and
conclusion on the same case. It is optional and separate from NetClaw's read-only
Convergence view (`convergence-read`), which needs none of this.

`setup/netclaw.py` adds, as one reversible unit, to the NetClaw host's OpenClaw:

| Piece | What it does |
|---|---|
| `alert-triage` skill | The investigation procedure (`integrations/netclaw/alert-triage`) |
| `convergence-case-mcp` | Tools to read the assigned case and site flows, request a JEV review and record findings |
| Agent `alert` | Runs hook investigations with only that skill, `read`, the case tools and the read-only tools you add |
| Hook `POST /hooks/alert` | Routes the receiver's hand-off to that agent, without chat delivery |

The interactive NetClaw agent is unchanged. The case API key and the hook token are
kept in `~/.openclaw/.env`; `openclaw.json` refers to them as `${VAR}`.

NetClaw's token and cost metrics (the NetClaw dashboard and the quota alerts) are a
separate, independent step: [agent-metrics.md](agent-metrics.md) installs their
exporter on the same host.

## Before you start

- The receiver runs on the NetClaw host: `setup/receiver.py status` shows it active
  (Docker: `docker.md`; K3s: `k3s.md` section 7).
- OpenClaw 2026.10 or newer, with more than one agent configured. For a single-agent
  install, adding an agent makes it a multi-agent fleet whose chat channels need
  explicit bindings; the helper stops and explains this unless `--allow-new-fleet`.
- An **operator** API key for the site in a private file (mode 600). The bundle's
  receiver key works (`NETWORK_GUARDIAN_TOKEN` in `receiver.env`); a dedicated key in
  the release's `API_KEYS` is better.
- Decide which read-only tools the agent may use, for example `prometheus-mcp__*`.
  Never add tools that change devices, run commands or send messages; the helper
  refuses the obvious ones.

## Plan, apply, verify

```bash
python3 setup/netclaw.py plan --api-url "$api_url" --site "$site" \
  --case-token-file "$key_file" --allow-tool 'prometheus-mcp__*' \
  --receiver-env "$bundle/receiver.env"
```

`plan` changes nothing. It lists the actions and the exact config patch, and stops on
conflicts: an existing `alert` agent or `alert` hook mapping it did not create, a hook
path other than `/hooks`, or a locally changed `alert-triage` skill. Show the plan to
the user and wait for approval. Then:

```bash
python3 setup/netclaw.py apply ...same options... --restart
python3 setup/receiver.py install --env-file "$bundle/receiver.env" --start
python3 setup/netclaw.py status
```

`apply` backs up `openclaw.json` and `.env` under `~/.openclaw/backups/`, checks the
key against the case API, installs the MCP server in its own virtualenv, creates the
agent through the running gateway (`agents.create`), then writes the rest of the config
only through `openclaw config patch` after a dry run. If a step fails, it deletes the
agent it just created and restores `.env`. `--restart` restarts
the OpenClaw gateway so it loads the hook token; without it, restart the gateway
yourself (this interrupts NetClaw's current conversations). Re-running the receiver
install picks up the gateway URL and hook token `apply` wrote into `receiver.env`.

Check that the pieces work, not only that they are configured:

- `openclaw mcp probe convergence-case-mcp` lists 7 tools.
- With DefenseClaw, `defenseclaw skill scan alert-triage` is CLEAN.
- `curl -s -o /dev/null -w '%{http_code}' -X POST http://127.0.0.1:18789/hooks/alert`
  returns 401 (hooks enabled, token required).

## Admit an alert and prove the loop

The policy (`INVESTIGATION_POLICY_PATH` in `receiver.env`) admits nothing at first.
Admit one harmless test alert at tier T2:

```yaml
allow_t2:
  - alertname: ConvergenceInvestigationTest
```

Fire it through the receiver as Alertmanager would (`ALERT_RECEIVER_TOKEN` from
`receiver.env`):

```bash
curl -s -X POST "http://127.0.0.1:8099/webhook" -H "Authorization: Bearer $receiver_token" \
  -H 'Content-Type: application/json' --data '{"status":"firing","receiver":"test","alerts":[{
  "status":"firing","fingerprint":"loop-test-1","labels":{"alertname":"ConvergenceInvestigationTest",
  "instance":"test-host","severity":"warning","site":"'"$site"'"},"annotations":{"summary":
  "Test alert for the NetClaw investigation loop","description":"Synthetic; no fault expected."}}]}'
```

Expect, within a few minutes: a case for the alert moves from `investigating` to
`resolved` or `escalated`, with notes that name the tools and time windows checked.
The gateway log shows `case_get` and `case_update` calls by the `alert` agent. If the
case stays `investigating`, the receiver escalates it at its deadline (15 minutes) and
logs why. Open real alerts to T2 one rule at a time, and keep budgets low while
watching noise and cost (`budgets` in the policy).

To check the human loop, choose **Need more** on the case in the HUD, or call
`POST /api/events/<id>/reinvestigate` with feedback: the case reopens and NetClaw
investigates again with your feedback.

## RAG priors and snapshots

The receiver searches prior investigations and stores resolved cases through NetClaw's
`rag-mcp`. Those libraries are installed in NetClaw's RAG runtime, not in the
receiver's virtualenv. Point the receiver at that interpreter when you install it:

```bash
python3 setup/receiver.py install --env-file "$bundle/receiver.env" \
  --netclaw-dir /path/to/netclaw \
  --rag-python /path/to/rag-runtime/bin/python3 --start
```

`--rag-python` must be an existing executable and requires `--netclaw-dir`. Take the
path from the `command` of `mcp.servers["rag-mcp"]` in `~/.openclaw/openclaw.json`
(keep that symlink: it is the runtime, not the system interpreter it points at).
Without `--rag-python`, the receiver imports `rag-mcp` in-process and those calls
fail when the runtime's modules are missing.

The receiver env file must carry the same `RAG_DATA_DIR`, `RAG_EMBEDDING_MODEL` and
`RAG_RERANK_ENABLED` as NetClaw's rag-mcp (`~/.openclaw/.env`), so searches use the
same store and embedding model. Each call loads that model, which takes seconds.
rag-mcp's own MCP server already opens the same store from another process.

## JEV reviews (optional)

For IDS cases, the agent can request an advisory JEV review through
`case_jev_review`. The receiver performs it with NetClaw's JEV client: install the
receiver with `--netclaw-dir` pointing at the NetClaw checkout and
`--jev-env-file` at the file holding NetClaw's JEV settings
(`CONVERGENCE_JEV_ENABLED=true`, `JEV_ENABLED=true` and the provider key), then see
`docs/runbooks/convergence-jev.md`. Without them, the review reports that JEV is not
enabled and the investigation continues without it.

## Undo

```bash
python3 setup/netclaw.py remove
```

It removes only what `apply` recorded: the MCP server, its hook mapping (and hooks,
if it enabled them and no other mapping remains), the unchanged skill, its `.env`
entries, and the agent if it created it. OpenClaw deletes agents only explicitly, so
`remove` uses `openclaw agents delete`: the shared workspace stays and the agent's
session history moves to Trash. OpenClaw finishes that deletion in the background;
wait a moment before applying again. The virtualenv is kept. Clear
`OPENCLAW_HOOK_TOKEN` in `receiver.env` and restart the gateway and the receiver.

Restoring `openclaw.json` from the `apply` backup by copying files does not remove an
agent: OpenClaw keeps agents in its own roster. Use `remove`.
