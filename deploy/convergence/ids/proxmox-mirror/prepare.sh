#!/usr/bin/env bash
# Run only after copying this directory and the shared shipper to the new root.
# Creates and validates the new instance; does not start it or touch ERSPAN.
set -euo pipefail
test "$(id -u)" = 0 || { echo 'Run as root in the IDS guest'; exit 1; }
cd /opt/netclaw-ids-proxmox
umask 077
source sensor.env
export IDS_INTERFACE IDS_INTERFACE_MAC
test "$IDS_SENSOR_ROOT" = /opt/netclaw-ids-proxmox
test "$IDS_CAPTURE_CONTAINER" = netclaw-suricata-proxmox
test "$IDS_CA_FILE" = /opt/netclaw-ids-proxmox/ca.crt
test "$IDS_TOKEN_FILE" = /opt/netclaw-ids-proxmox/sensor-token
test -s "$IDS_CA_FILE"
test -s "$IDS_TOKEN_FILE"
test -s shipper.py
if docker inspect netclaw-suricata-proxmox >/dev/null 2>&1; then
  echo 'New container already exists; inspect it before explicit replacement.'
  exit 1
fi
image='jasonish/suricata@sha256:bfcc801424f8b3b39ea1d95a76bb198e6ea9124b166185692042e4055405436c'
mkdir -p config logs
chmod 700 . logs
chmod 600 sensor.env "$IDS_CA_FILE" "$IDS_TOKEN_FILE"
python3 capture-interface.py
# Copy support files, but derive a fresh YAML without modifying the old tree.
cp -a /opt/netclaw-ids/config/. config/
python3 build-config.py --base /opt/netclaw-ids/config/suricata.yaml \
  --output config/suricata.yaml --interface "$IDS_INTERFACE"
# Share rule health without writing to the ERSPAN instance.
ln -s /opt/netclaw-ids/rules-status.json rules-status.json
docker image inspect "$image" >/dev/null
mounts=(-v /opt/netclaw-ids-proxmox/config:/etc/suricata:ro
  -v /opt/netclaw-ids/rules:/var/lib/suricata/rules:ro
  -v /opt/netclaw-ids-proxmox/logs:/var/log/suricata)
limits=(--cpus "${IDS_CAPTURE_CPUS:-2}" --memory "${IDS_CAPTURE_MEMORY:-3g}"
  --memory-swap "${IDS_CAPTURE_MEMORY:-3g}")
# Same caps during validation: a successful parse alone does not prove the
# guest has enough free memory to run both engines continuously.
docker run --rm --network none "${limits[@]}" --read-only --tmpfs /run --tmpfs /tmp \
  "${mounts[@]}" --entrypoint suricata "$image" -T -c /etc/suricata/suricata.yaml
docker create --name netclaw-suricata-proxmox --network host --restart no \
  "${limits[@]}" --cap-drop ALL --cap-add NET_RAW --cap-add NET_ADMIN --cap-add SYS_NICE \
  --security-opt no-new-privileges --read-only --tmpfs /run --tmpfs /tmp \
  --log-opt max-size=10m --log-opt max-file=3 "${mounts[@]}" \
  --entrypoint suricata "$image" -c /etc/suricata/suricata.yaml --af-packet="$IDS_INTERFACE"
install -m 644 netclaw-ids-proxmox-*.service /etc/systemd/system/
systemctl daemon-reload
echo 'Prepared only. Review memory and isolated NIC, then explicitly start the new units.'
