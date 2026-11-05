#!/bin/sh
# Invoke with: sudo sh build-image.sh from the build/ directory.
# GitHub contents API does not preserve executable mode for initial files,
# so call the auto scripts with 'sh', rather than relying on the execute bit.
set -eu
cd "$(dirname "$0")"
if [ "$(id -u)" -ne 0 ]; then
  echo "live-build must run as root inside an isolated builder" >&2
  exit 1
fi
sh auto/config
# Do not use a pipeline without pipefail; preserve the exit status of lb build.
lb build
