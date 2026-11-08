#!/bin/sh
# Verify the actual kiosk entrypoint fails closed before Xorg/Kodi starts.
set -eu
script=build/config/includes.chroot/usr/local/bin/dede-kodi-kiosk
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
mkdir -p "$tmp/runtime"
# The packaged skin is not present on the CI runner. Fail before starting
# Xorg; this also catches accidental removal of the skin preflight.
if XDG_RUNTIME_DIR="$tmp/runtime" sh "$script" >"$tmp/output" 2>&1; then
  echo 'Kiosk unexpectedly started without the Dede TV skin' >&2
  exit 1
fi
grep -Eq 'Dede TV skin|skin|profile' "$tmp/output"
# A missing runtime directory must be rejected before profile setup.
if XDG_RUNTIME_DIR="$tmp/missing" sh "$script" >"$tmp/runtime-output" 2>&1; then
  echo 'Kiosk accepted an absent runtime directory' >&2
  exit 1
fi
grep -q 'Kodi runtime directory is unavailable' "$tmp/runtime-output"
echo 'Kiosk fail-closed smoke checks passed'
