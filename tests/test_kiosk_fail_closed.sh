#!/bin/sh
# Verify the actual kiosk entrypoint fails closed before Xorg/Kodi starts.
set -eu
script=build/config/includes.chroot/usr/local/bin/dede-kodi-kiosk
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
mkdir -p "$tmp/runtime"
chmod 700 "$tmp/runtime"
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
# A relative runtime path can resolve differently across working directories.
# Reject it before profile initialization or Xorg startup.
if (cd "$tmp" && XDG_RUNTIME_DIR=runtime sh "$OLDPWD/$script") >"$tmp/relative-output" 2>&1; then
  echo 'Kiosk accepted a relative runtime directory' >&2
  exit 1
fi
grep -q 'Kodi runtime directory must be absolute' "$tmp/relative-output"
# A world-writable runtime is not safe even when it is otherwise writable.
mkdir -p "$tmp/shared-runtime"
chmod 777 "$tmp/shared-runtime"
if XDG_RUNTIME_DIR="$tmp/shared-runtime" sh "$script" >"$tmp/shared-output" 2>&1; then
  echo 'Kiosk accepted a shared runtime directory' >&2
  exit 1
fi
grep -q 'mode 0700' "$tmp/shared-output"
# Reject a group-accessible runtime directory too.
chmod 750 "$tmp/shared-runtime"
if XDG_RUNTIME_DIR="$tmp/shared-runtime" sh "$script" >"$tmp/group-output" 2>&1; then
  echo 'Kiosk accepted a group-accessible runtime directory' >&2
  exit 1
fi
grep -q 'mode 0700' "$tmp/group-output"
# A regular file is not a usable socket directory, even when writable.
printf 'not a directory\n' > "$tmp/runtime-file"
if XDG_RUNTIME_DIR="$tmp/runtime-file" sh "$script" >"$tmp/file-output" 2>&1; then
  echo 'Kiosk accepted a regular file as runtime directory' >&2
  exit 1
fi
grep -q 'Kodi runtime directory is unavailable' "$tmp/file-output"
# The kiosk must not start when the runtime directory has no owner access.
mkdir -p "$tmp/no-owner-runtime"
chmod 000 "$tmp/no-owner-runtime"
if XDG_RUNTIME_DIR="$tmp/no-owner-runtime" sh "$script" >"$tmp/no-owner-output" 2>&1; then
  echo 'Kiosk accepted an inaccessible runtime directory' >&2
  exit 1
fi
grep -Eq 'unavailable|mode 0700' "$tmp/no-owner-output"
echo 'Kiosk fail-closed smoke checks passed'
