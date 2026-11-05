#!/bin/sh
# Invoke with: sudo sh build-image.sh from the build/ directory.
set -eu
cd "$(dirname "$0")"
if [ "$(id -u)" -ne 0 ]; then
  echo "live-build must run as root inside an isolated builder" >&2
  exit 1
fi
sh auto/config
# Preserve lb build's exit code while capturing diagnostics for CI.
# Bash pipefail is required because POSIX sh pipelines return tee's status.
bash auto/build
