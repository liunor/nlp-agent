#!/usr/bin/env bash
# Only the dedicated test bridge directory is provisioned here.
set -euo pipefail
bridge_dir=/var/lib/nova/test/files-bridge
test -c /dev/fuse || { echo 'Missing /dev/fuse on Docker host' >&2; exit 1; }
sudo -n mkdir -p "$bridge_dir"
if ! mountpoint -q "$bridge_dir"; then
  sudo -n mount --bind "$bridge_dir" "$bridge_dir"
fi
sudo -n mount --make-rshared "$bridge_dir"
propagation="$(findmnt -n -o PROPAGATION --target "$bridge_dir")"
[[ "$propagation" == *shared* ]] || { echo 'Bridge mount is not shared' >&2; exit 1; }
export NOVA_SANDBOX_BRIDGE_DIR="$bridge_dir"
printf '%s\n' "$bridge_dir"
