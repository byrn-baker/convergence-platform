#!/usr/bin/env bash
# Update staged rules, validate them with the deployed engine, then promote.
set -euo pipefail
cd /opt/netclaw-ids
exec 9>/opt/netclaw-ids/update-rules.lock
flock -n 9 || exit 0
source /opt/netclaw-ids/image.env
mkdir -p staging-rules
docker run --rm --network host --entrypoint suricata-update \
  -v /opt/netclaw-ids/staging-rules:/var/lib/suricata/rules \
  "$SURICATA_IMAGE" --no-test --no-reload
test -s staging-rules/suricata.rules
docker run --rm --network none --entrypoint suricata \
  -v /opt/netclaw-ids/config:/etc/suricata:ro \
  -v /opt/netclaw-ids/staging-rules:/var/lib/suricata/rules:ro \
  "$SURICATA_IMAGE" -T -c /etc/suricata/suricata.yaml
cp staging-rules/suricata.rules rules/suricata.rules.new
if [ -f rules/suricata.rules ]; then cp rules/suricata.rules rules/suricata.rules.previous; fi
mv rules/suricata.rules.new rules/suricata.rules
# A cold restart is explicit and supported across engine versions; it creates a
# short detection gap once a day. If unchanged, avoid the restart.
if ! cmp -s rules/suricata.rules rules/suricata.rules.previous; then
  if docker inspect netclaw-suricata >/dev/null 2>&1; then systemctl restart netclaw-ids-capture; fi
fi
python3 - <<'PY'
import json,time
from pathlib import Path
root=Path('/opt/netclaw-ids')
count=sum(1 for line in (root/'rules/suricata.rules').open() if line.startswith(('alert ', 'drop ', 'reject ', 'pass ')))
if count == 0: raise SystemExit('No detection signatures loaded')
temporary=root/'rules-status.json.new'
temporary.write_text(json.dumps({'rules_count':count,'rules_checked_at':time.time()}))
temporary.replace(root/'rules-status.json')
PY
