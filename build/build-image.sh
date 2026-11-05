#!/bin/sh
# Invoke with: sudo sh build-image.sh from the build/ directory.
set -eu
cd "$(dirname "$0")"
if [ "$(id -u)" -ne 0 ]; then
  echo "live-build must run as root inside an isolated builder" >&2
  exit 1
fi
# Surface the live-system login prompt on QEMU's serial port so the CI
# verifies real userspace boot rather than relying on an opaque VGA console.
# This is diagnostic access only; production images require a separate policy.
serial_wants=config/includes.chroot/etc/systemd/system/getty.target.wants
mkdir -p "$serial_wants"
ln -sfn /lib/systemd/system/serial-getty@.service \
  "$serial_wants/serial-getty@ttyS0.service"
sh auto/config
# Preserve lb build's exit code while capturing diagnostics for CI.
# Bash pipefail is required because POSIX sh pipelines return tee's status.
bash auto/build
