#!/usr/bin/env bash
# Run on the new sensor VM after copying this directory to /opt/netclaw-ids.
# Requires image.env and sensor.env; does not configure any switch.
set -euo pipefail
test "$(id -u)" = 0 || { echo 'Run with sudo on the dedicated sensor VM'; exit 1; }
cd /opt/netclaw-ids
umask 077
source image.env
source sensor.env
: "${SURICATA_IMAGE:?Use a digest-pinned Suricata image}"
[[ "$SURICATA_IMAGE" == *@sha256:* ]] || { echo 'Image must be digest pinned'; exit 1; }
: "${IDS_INTERFACE:?}" "${IDS_SWITCH_IP:?}" "${IDS_SENSOR_IP:?}" "${IDS_HOME_NET:?}"
if docker inspect netclaw-suricata >/dev/null 2>&1; then
  echo 'Sensor container already exists; use the documented upgrade procedure.'
  exit 1
fi
apt-get update
DEBIAN_FRONTEND=noninteractive apt-get install -y docker.io python3-yaml ethtool tcpdump qemu-guest-agent
systemctl enable --now docker qemu-guest-agent
# ERSPAN adds headers to the original frame. This transport path must support
# the larger frame end-to-end; do not apply this to a shared production host.
IDS_INTERFACE="$IDS_INTERFACE" python3 - <<'PY'
import os,yaml
from pathlib import Path
p=Path('/etc/netplan/60-netclaw-ids-mtu.yaml')
p.write_text(yaml.safe_dump({'network':{'version':2,'ethernets':{os.environ['IDS_INTERFACE']:{'mtu':9000}}}}))
p.chmod(0o600)
PY
netplan generate
ip link set dev "$IDS_INTERFACE" mtu 9000
mkdir -p config rules staging-rules logs
chmod 700 . logs
chown root:root .
docker pull "$SURICATA_IMAGE"
container=$(docker create "$SURICATA_IMAGE")
trap 'docker rm "$container" >/dev/null 2>&1 || true' EXIT
docker cp "$container:/etc/suricata/." config/
cp config/suricata.yaml config/upstream.yaml
cp local.rules config/local.rules
python3 render-sensor.py --base config/upstream.yaml --output config/suricata.yaml \
  --interface "$IDS_INTERFACE" --switch-ip "$IDS_SWITCH_IP" --sensor-ip "$IDS_SENSOR_IP" \
  --home-net "$IDS_HOME_NET"
bash update-rules.sh
docker create --name netclaw-suricata --network host --restart no \
  --cap-drop ALL --cap-add NET_RAW --cap-add NET_ADMIN --cap-add SYS_NICE \
  --security-opt no-new-privileges --read-only --tmpfs /run --tmpfs /tmp \
  --log-opt max-size=10m --log-opt max-file=3 \
  -v /opt/netclaw-ids/config:/etc/suricata:ro \
  -v /opt/netclaw-ids/rules:/var/lib/suricata/rules:ro \
  -v /opt/netclaw-ids/logs:/var/log/suricata \
  --entrypoint suricata "$SURICATA_IMAGE" -c /etc/suricata/suricata.yaml --af-packet="$IDS_INTERFACE"
install -m 644 netclaw-ids-*.service netclaw-ids-rules.timer /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now netclaw-ids-capture netclaw-ids-shipper netclaw-ids-rules.timer netclaw-ids-probe
