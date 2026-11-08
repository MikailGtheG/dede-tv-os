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
# Reject a symlinked runtime path even when its target is writable.
# Otherwise a redirected runtime could be accepted before profile setup.
mkdir -p "$tmp/redirected-runtime"
ln -s "$tmp/redirected-runtime" "$tmp/runtime-link"
if XDG_RUNTIME_DIR="$tmp/runtime-link" sh "$script" >"$tmp/symlink-output" 2>&1; then
  echo 'Kiosk accepted a symlinked runtime directory' >&2
  exit 1
fi
grep -q 'Kodi runtime directory is unavailable' "$tmp/symlink-output"
echo 'Kiosk fail-closed smoke checks passed'
