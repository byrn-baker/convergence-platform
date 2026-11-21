#!/usr/bin/env bash
# Owns only one new disposable container and a new synthetic evidence directory.
set -euo pipefail
umask 077
if [[ $# != 3 ]]; then
  printf 'Usage: %s LOCAL_IMAGE_ID NEW_OUTPUT_DIRECTORY 150|300\n' "$0" >&2
  exit 2
fi
image=$1
output=$(realpath -m -- "$2")
rate=$3
[[ "$rate" == 150 || "$rate" == 300 ]] || { echo 'Rate must be150or300' >&2; exit 2; }
[[ ! -e "$output" && ! -L "$output" ]] || { echo 'Output must be new; preserving existing evidence' >&2; exit 2; }
repo=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)
# Verify availability without a pull or use of any registry credential.
docker image inspect "$image" >/dev/null
mkdir -p -- "$(dirname -- "$output")"
mkdir -m 700 -- "$output"
cid=''
cleanup() { if [[ -n "$cid" ]]; then docker rm -f "$cid" >/dev/null; fi; }
trap cleanup EXIT
command=(docker create --pull=never --network none --cpus 1 --memory 512m --memory-swap 512m
  --pids-limit 128 --read-only --cap-drop ALL --security-opt no-new-privileges
  --user "$(id -u):$(id -g)" --tmpfs /tmp:rw,noexec,nosuid,size=64m
  -e PYTHONDONTWRITEBYTECODE=1 -v "$repo:/src:ro" -v "$output:/evidence:rw"
  "$image" python /src/tests/stabilization/ids-load.py --output /evidence/run --rate "$rate")
printf '%q ' "${command[@]}" > "$output/command.txt"
printf '\n' >> "$output/command.txt"
cid=$("${command[@]}")
[[ "$cid" =~ ^[a-f0-9]{64}$ ]] || { echo 'Unexpected container identity' >&2; exit 1; }
printf '%s\n' "$cid" > "$output/container-id"
docker inspect "$cid" > "$output/container-before.json"
set +e
docker start --attach "$cid" > "$output/run.log" 2>&1
attach_rc=$?
set -e
docker inspect "$cid" > "$output/container-after.json"
python3 - "$output" "$attach_rc" <<'PY'
import json,sys
from pathlib import Path
out=Path(sys.argv[1]);before=json.loads((out/'container-before.json').read_text())[0]
after=json.loads((out/'container-after.json').read_text())[0]
h=before['HostConfig']
assert h['Memory']==512*1024**2 and h['MemorySwap']==h['Memory'] and h['NanoCpus']==10**9
assert h['NetworkMode']=='none' and h['ReadonlyRootfs'] and h['CapDrop']==['ALL']
assert all(m['RW'] is False for m in before['Mounts'] if m['Destination']=='/src')
assert not after['State']['OOMKilled'] and after['State']['ExitCode']==0 and sys.argv[2]=='0', 'Load process failed; retain logs'
summary=json.loads((out/'run/summary.json').read_text());assert summary['status']=='PASS'
print('PASS: '+str(out/'run/summary.json'))
PY
