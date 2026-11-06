#!/usr/bin/env bash
# Called after the shared updater succeeds; never updates shared rule files.
set -euo pipefail
cd /opt/netclaw-ids-proxmox
umask 077
source sensor.env
exec 8>/opt/netclaw-ids-proxmox/rules-refresh.lock
flock -n 8 || exit 0
# Keep the next updater from replacing files during validation/load.
exec 9</opt/netclaw-ids/update-rules.lock
flock -s 9
digest=$(sha256sum /opt/netclaw-ids/rules/suricata.rules config/suricata.yaml config/local.rules | sha256sum | cut -d ' ' -f 1)
if test -f loaded-rules.sha256 && test "$(cat loaded-rules.sha256)" = "$digest"; then exit 0; fi
# Do not resurrect an explicitly stopped/pilot-disabled capture. On first
# production start, run this helper after capture starts to record its digest.
systemctl is-active --quiet netclaw-ids-proxmox-capture.service || exit 0
# Avoid loading a third full ruleset alongside both running engines. Only the
# additional capture is stopped; failed validation leaves it stopped for review.
systemctl stop netclaw-ids-proxmox-capture.service
image='jasonish/suricata@sha256:bfcc801424f8b3b39ea1d95a76bb198e6ea9124b166185692042e4055405436c'
docker run --rm --network none --read-only --tmpfs /run --tmpfs /tmp \
  --cpus "${IDS_CAPTURE_CPUS:-2}" --memory "${IDS_CAPTURE_MEMORY:-3g}" \
  --memory-swap "${IDS_CAPTURE_MEMORY:-3g}" \
  -v /opt/netclaw-ids-proxmox/config:/etc/suricata:ro \
  -v /opt/netclaw-ids/rules:/var/lib/suricata/rules:ro \
  -v /opt/netclaw-ids-proxmox/logs:/var/log/suricata \
  --entrypoint suricata "$image" -T -c /etc/suricata/suricata.yaml
systemctl start netclaw-ids-proxmox-capture.service
# A successful systemd start alone does not prove initialization completed.
sleep 5
systemctl is-active --quiet netclaw-ids-proxmox-capture.service
test "$(docker inspect --format '{{.State.Running}}' netclaw-suricata-proxmox)" = true
printf '%s\n' "$digest" > loaded-rules.sha256.new
mv loaded-rules.sha256.new loaded-rules.sha256
