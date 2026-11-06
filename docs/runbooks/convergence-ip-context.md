# Public-IP context for Convergence IDS

Enabled on the home deployment on **25 October 2026** using the existing IPinfo
credential. IPinfo Lite provides country, continent, ASN, ASN organization and
organization domain in one local MMDB database. MaxMind's open-source MMDB reader
reads the file; the actual data provider is **IPinfo**, not MaxMind GeoLite.
No new subscription or per-address web lookup was added.

## What an observation now tells you

Both source and destination get a separate `ip_context` snapshot at ingestion:

- `internal`: matches a configured internal prefix, including an owned public
  prefix if explicitly configured;
- `non_public`: private/special-use, loopback, link-local, multicast, reserved or
  otherwise non-global address outside the configured internal prefixes;
- `external`: public unicast eligible for local lookup, with an explicit
  `enriched`, `not_found` or `unavailable` result.

The snapshot records provider, database build time, enrichment time and database
health. Missing data stays unknown. The data describes network infrastructure;
it does not identify a person, prove maliciousness or identify the customer of a
shared cloud/CDN address. Country is approximate; an anycast address can serve
traffic in many places. The ASN organization's domain is **not** the hostname a
client requested. Existing DNS/TLS observations remain separate evidence.

Neither geography nor network ownership changes case severity, investigation
policy or firewall rules. Existing AbuseIPDB and OTX capabilities can provide
additional reputation evidence during an investigation; this change does not
start sending every observed address to them.

## Where the context appears

- **HUD → Convergence → Diary:** expand “Source and destination context” within
  a new IDS case's message. Triage shows the same snapshot for escalated cases.
- **HUD → Convergence → Security:** displays local database availability.
- **VictoriaLogs:** structured `ip_context.source.*` and
  `ip_context.destination.*` fields alongside original `eve` JSON and `event_id`.
- **Loki:** `netclaw_ip_context` within the EVE log document. IPs, countries, ASNs
  and organization names are not added as Loki stream labels or Prometheus labels.
- **VictoriaMetrics / Prometheus:** `netclaw_ids_ip_context_ready` and
  `netclaw_ids_ip_context_database_timestamp_seconds` report database health.

Example VictoriaLogs query:

```bash
curl -fsSG http://127.0.0.1:9428/select/logsql/query \
  --data-urlencode 'query=job:ids-eve ip_context.destination.scope:external _time:1h | stats by (ip_context.destination.asn, ip_context.destination.as_name, ip_context.destination.country_code) count()'
```

The original EVE JSON, event hash and case correlation key are unchanged.
Context lives in a separate inbox column and case annotation. Retries reuse the
persisted snapshot, so a later database update cannot rewrite an earlier case's
meaning. Events ingested before this feature remain without enrichment; they
are not reissued as fresh alerts or silently rewritten. The case display reflects
the first observation retained by the existing five-minute correlation window.

## Installation

1. Install the ingestion dependencies:

   ```bash
   .venv/bin/python -m pip install -r services/ids-ingest/requirements.txt
   ```

2. Create `deploy/convergence/secrets/ids/ipinfo.env` containing only
   `IPINFO_TOKEN=...`, with mode `0600`, owned by the service user. On the home
   deployment the token was copied from the already configured `.env`; never
   commit it or put it in a command-line download URL. The updater service loads
   this private environment file.
3. Create the database directory and install the service/timer templates. Adjust
   the `ubuntu` user, repository path and state directory for another host:

   ```bash
   sudo install -d -o ubuntu -g ubuntu -m 0700 /var/lib/netclaw-ids-ingest/geoip
   sudo install -m 0644 deploy/convergence/ids/netclaw-ids-ip-context.service \
     deploy/convergence/ids/netclaw-ids-ip-context.timer /etc/systemd/system/
   sudo systemctl daemon-reload
   sudo systemctl start netclaw-ids-ip-context.service
   sudo systemctl enable --now netclaw-ids-ip-context.timer
   ```

4. Add this block to the **private** IDS `config.json`, using your actual internal
   prefixes (including any public prefixes you own and consider internal):

   ```json
   "ip_enrichment": {
     "database": "/var/lib/netclaw-ids-ingest/geoip/ipinfo_lite.mmdb",
     "internal_networks": ["192.168.3.0/24", "192.168.13.0/24", "10.42.0.0/16"]
   }
   ```

   The live home config includes all ten selected VLAN networks, the K3s pod
   network and local IPv6 ranges. Non-public addresses never receive public-IP
   attribution, even if their subnet was omitted here.
5. Restart ingest, rebuild/recreate Convergence API with **both IDS overlays**
   from the [Victoria runbook](convergence-ids-victoria.md), and rebuild/restart
   the HUD. Reload the Prometheus rules after `promtool check rules` succeeds.
   The API change projects the persisted context into the existing diary
   response; it requires no PostgreSQL migration.

## Updates and failure behavior

`netclaw-ids-ip-context.timer` runs daily at 04:35 UTC plus up to 30 minutes of
jitter. It downloads the full database, never an individual observed address.
The updater limits download size, validates MMDB type/build time and IPv4/IPv6
country/ASN records, refuses a downgrade from a valid newer database, and
atomically replaces the file only after validation. Failures preserve the last
good file and log an error type/status without exposing credential-bearing URLs.

The reader checks for replacement at most once a minute, then swaps readers and
clears its bounded cache. No capture or ingest restart is needed for routine
updates. A failed reload keeps a previous valid reader but reports degraded
health. A database older than seven days is reported stale. With no usable
file, ingestion and detection continue and external context is unavailable.
The `IDSIPContextUnavailable` rule alerts after 15 minutes with
`investigate=false`.

Only the updater receives the IPinfo token. The ingest process performs local
lookups without that token. The file is about 25 MB in the recorded deployment.
Enriched event records take more storage than raw events; include this growth
when sizing VictoriaLogs and the local inbox. No K3s migration was performed.

## Verification

Require all of the following:

1. The updater service succeeds; its log reports a valid database build time.
2. Security reports active enrichment and all existing capture sources remain
   active. The two health metrics reach both metrics stores.
3. A fresh IDS observation with a public endpoint gets country and ASN context
   in the inbox, Loki, VictoriaLogs and the corresponding case. The original
   event identity and JSON still match.
4. A private endpoint is marked internal/non-public and has no invented public
   ASN or country. The HUD escapes database-provided text and uses a fixed
   attribution link.

Run `.venv/bin/python -m unittest discover -s services/ids-ingest -q` and
`node --test test/convergence-ids.mjs` from `ui/netclaw-visual` for the UI checks.
Tests cover IPv4/IPv6 and mapped addresses, special/private ranges, owned public
prefixes, caching and reloads, stale/missing data, invalid download preservation,
replay-stable context, evidence identity and HTML injection handling.

## Recorded acceptance — 25 October 2026

The local database validated successfully (25,787,290 bytes, build epoch
1792829046). An ICMP commissioning canary to public resolver `1.1.1.1` produced
case `0d42516f-5128-4669-88b1-4fba4b3ba924`, with destination `AS13335`,
`Cloudflare, Inc.` and `cloudflare.com`; the internal source remained internal.
The same context reached Loki and VictoriaLogs. The commissioning case was
resolved after validation. The database's AU country attribution for this
anycast address is not proof that the traffic traveled to Australia.

Both Prometheus and VictoriaMetrics reported enrichment ready. The daily timer
is enabled. All three capture sources stayed active, and ingestion continued.
Validation passed: 24 ingestion/enrichment tests, four Convergence API tests,
four HUD tests, the existing two alert-rule scenarios and a HUD production build.
The documented VictoriaLogs query returned real ASN/organization groups.

## Data attribution and limitations

IP address data is powered by [IPinfo](https://ipinfo.io). IPinfo publishes Lite
under [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/); retain the
provider attribution and applicable data licensing when redistributing it.
The HUD includes attribution wherever it displays this enrichment.

Primary references: [IPinfo Lite database and schema](https://ipinfo.io/developers/ipinfo-lite-database),
[IPinfo Lite licensing and scope](https://ipinfo.io/lite), and the
[MaxMind MMDB reader](https://github.com/maxmind/MaxMind-DB-Reader-python).

To disable enrichment, remove `ip_enrichment` from the private configuration and
restart ingest; stop/disable the updater timer if no longer needed. Previously
recorded context stays with its original evidence.
