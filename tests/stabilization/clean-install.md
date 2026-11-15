# Stock component and HTTPS workspace qualification

These two fixtures qualify components of an explicitly isolated installation.
They do not install a complete NetClaw/OpenClaw host or qualify a production
cluster. Never supply production credentials or map the browser fixture to a
production origin.

`clean-install-reader.py` uses the stock NetClaw component installer and MCP
registration helper twice in a private state directory. It then calls all three
real MCP tools against a loopback-forwarded Convergence API, checking retained
same-window flow evidence, synthetic VM labels and scope/credential denials.
It requires a synthetic `demo` site with exporter `127.0.0.1`, recent retained
records, VM demo enabled, and a dedicated viewer token file. Run it with the
installed connector Python containing its pinned FastMCP dependencies:

```sh
"$CONNECTOR_PYTHON" tests/stabilization/clean-install-reader.py \
  --stock "$NETCLAW_STOCK" --state "$STATE" \
  --api http://127.0.0.1:13082 --token-file "$STATE/bundle/reader-token" \
  --output "$STATE/reader-before.json"
```

`clean-install-workspace.mjs` exercises an actual lab HTTPS deployment at
`https://reviewer.test:13444`, mapped to loopback by Chromium. The caller prepares
an NSS trust database under `$STATE/browser-home`, a synthetic account
`clean-operator` with password `synthetic-stabilization-password` and operator
access only to `demo`, and `review-case.json` containing the synthetic security
case's `case_id`. `PLAYWRIGHT_MODULE` names the installed Playwright entry point;
`REVIEWER_CHROMIUM` names the reviewed browser executable. It does not ignore
certificate errors. Optional browser library paths must be supplied explicitly.

```sh
node tests/stabilization/clean-install-workspace.mjs "$STATE" before
# Restart only the disposable deployment, wait for readiness, and restore its
# loopback forwards. Do not reinsert the case, receipt or flow records.
node tests/stabilization/clean-install-workspace.mjs "$STATE" after
```

The first phase creates one attributed disposition. The second reads that exact
receipt without creating another operation. Both check telemetry, case/overview
UI, CSRF rejection, site denial, native-route denial and logout revocation.
Flow records must still fall within the fixture's 15-minute observation window.

## Executed baseline, November 15, 2026

The first stabilization run used stock NetClaw
`a5e7c3a3e07d8c8c4f2f26d22b4bd854dd1ba42e` and Convergence release
`96877967f7311894b6559d9655a5aef6972669d5` (`v0.1.15`). A disposable K3s
`v1.35.6+k3s1` container used a separate kubeconfig, loopback API binding,
3 GiB memory cap and one CPU cap. No native HUD patch was applied.

Executed checks passed:

- Stock component install/reinstall, native MCP registration, three real tools
  and viewer denial checks against the current K3s service.
- Helm PostgreSQL, API, VictoriaMetrics, VictoriaLogs, agent and dedicated
  HTTPS reviewer API/HUD. The reviewer used a separate restricted database
  role with a 12-second statement timeout and newly generated synthetic PKI.
- Trusted browser sign-in, real retained telemetry, case/disposition and
  boundary checks using the workspace fixture.
- The existing complete reviewer browser fixture against the lab PostgreSQL
  and stock JEV core: real HTTPS, relay, receiver and durable receipts, with
  synthetic Gateway and provider exchanges.
- Whole lab node restart: seven workloads became Ready within 32.3 seconds
  by their condition timestamps. Two cases, one disposition/operation and one
  account remained; exact receipt and three original flow records were verified.

An empty isolated OpenClaw `2026.10.9 (8b18b55)` process passed startup/health,
with hooks and heartbeat disabled, then stopped. This reused its installed
binary and did not prove a fresh runtime installation or agent completion.

Stock RAG source passed snapshot, matching retrieval in a new worker and
supersession using a fresh private store and a network namespace with no
external interface. It reused existing dependency wheels and copied only public
cached BAAI/bge-small-en-v1.5 weights. Local embedding ran; no paid provider or
agent/JEV evaluation ran. The snapshot explicitly reported GAIT audit unavailable
because the private component checkout had no initialized GAIT repository.
Data-operation success does not close that audit gap.

Resolved RAG libraries were FastMCP 4.0.11, MCP 2.3.0, torch 2.10.0+cpu,
transformers 5.22.2, sentence-transformers 5.7.0, chromadb 1.5.9 and
rank-bm25 0.2.2. Reranking was disabled. Fresh dependency installation remains open.

Private evidence is under
`~/.local/state/convergence-stabilization-20261115/clean/`: the qualification
summary records resolved image digests, dependency inventories, explicit gaps
and three browser prerequisites corrected before the successful run. The browser
used cached headless-shell build 1208 and existing unpacked libraries; this is
not fresh browser/system-package qualification. One synthetic CA was used for
the lab's TLS roles; production CA separation was not rehearsed here.

S03 remains partial: the full top-level host installer, fresh runtime/RAG
dependencies, actual Gateway completion, full collectors/alerting/Loki/Grafana
profile, physical telemetry and production ingress still need qualification.
Node restart is persistence evidence, not backup restoration or a recovery SLA.
