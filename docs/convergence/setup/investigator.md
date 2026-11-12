# Alert investigator member

[netclaw.md](netclaw.md) gives NetClaw a restricted `alert` agent that investigates
admitted cases itself. On a NetClaw **Border** (an iN2N Risk), the investigation can
run instead in its own member: a separate OpenClaw home with only the investigation's
servers, skills and tools, its own memory, and only the variables those servers use.
The member is on-demand: the Border starts it for the first routed task and it exits
when idle.

The member is defined by a NetClaw **member spec**,
[integrations/netclaw/investigator.json](../../../integrations/netclaw/investigator.json):

| | |
|---|---|
| Servers | `convergence-case-mcp`, `prometheus-mcp`, `grafana-mcp`, `pyats-mcp`, `pfsense-mcp`, `unifi-network`, `greynoise-community-mcp`, `rag-mcp` (plus NetClaw's base `memory-mcp` and `gait-mcp`) |
| Skills | `alert-triage`, `prometheus-monitoring`, `grafana-observability`, `pyats-network`, `pyats-troubleshoot`, `pfsense-threat-intel` |
| Tools | `read`, `session_status`, the case tools, and read, list, search and query tools of the evidence servers (29 entries). Nothing that writes files, runs commands, changes devices or sends messages |
| Model | NetClaw's member tier (`--model` picks an Anthropic model) |

`setup/investigator.py` provisions it with NetClaw's own tools; it adds no NetClaw code.

## Before you start

- NetClaw 1.21.0 or later (member specs), on a Border: `netclaw risk status` shows
  `role: border` and the mesh daemon is running.
- [netclaw.md](netclaw.md) applied on the Border, so it has `convergence-case-mcp`
  and the `alert-triage` skill. The member uses the same case API key.
- The Border has the evidence servers you want the member to use. A spec server the
  Border does not have is left out, with its tools and skills; `plan` lists them.
  [network-mcps.md](network-mcps.md) installs `pfsense-mcp` and `unifi-network`.
- An Anthropic API key in the Border's `.env` (`ANTHROPIC_API_KEY`): spec members use
  the Anthropic provider.

## Plan, apply, verify

```bash
python3 setup/investigator.py plan  --netclaw-dir ~/netclaw
python3 setup/investigator.py apply --netclaw-dir ~/netclaw
python3 setup/investigator.py status
```

`plan` changes nothing. It shows the member id (`<risk>/alert-investigator`), its home
(`~/.openclaw-<risk>-alert-investigator`), the servers, skills and tools it keeps, and
NetClaw's own validation of the spec against this Border. It stops on conflicts: no
member spec support, no `convergence-case-mcp`, not a Border, or a member or home of
that name that this helper did not create. Show the plan to the user and wait for
approval.

`apply` runs, in order:

1. `in2n-migrate.py --spec … --specs-only`: stages the member's least-privilege `.env`
   (only the variables its servers reference) and its launcher, under
   `~/.local/state/convergence/investigator-alert-investigator/staging/`;
2. `in2n-member-home.py --spec …`: builds the member's OpenClaw home with only the
   spec's servers, skills and tool allowlist;
3. registers the member on-demand with the mesh daemon, with its launcher as the launch
   command, and writes its enrollment token into the staged `.env` (mode 600). The token
   is never printed.

Test the member by routing a question to it; the Border starts it:

```bash
netclaw risk route "Which devices reported interface errors in the last hour?" alert-triage
netclaw risk members        # alert-investigator: active after its first task
```

## Let the alert agent delegate

The `alert` agent from [netclaw.md](netclaw.md) delegates to the member when it has
NetClaw's federation tools. Give it those and no evidence tools, so every investigation
runs in the member with the member's narrower tools:

```bash
python3 setup/netclaw.py apply ...same options, without evidence --allow-tool... \
  --allow-tool n2n-mcp__n2n_route --allow-tool n2n-mcp__n2n_task_wait --restart
```

With `n2n_route`, the `alert-triage` skill (1.1.0) makes the agent a dispatcher: it routes
the case with `target_hint="alert-triage"`, waits for the member's task and reads back
the case the member recorded. An agent that also has evidence tools may still be tempted
to investigate itself; the skill tells it not to.
Prove the loop as in [netclaw.md](netclaw.md#admit-an-alert-and-prove-the-loop): the
member's gateway log, not the Border's, shows the `case_get` and `case_update` calls.

## Remove

```bash
python3 setup/investigator.py remove
```

`remove` unregisters the member, then deletes only what `apply` created: the member's
home and the staging directory. It keeps a directory whose recorded path no longer
matches what `apply` would have created.
