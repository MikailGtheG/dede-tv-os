#!/bin/sh
# Verify the actual kiosk entrypoint fails closed before Xorg/Kodi starts.
set -eu
script=build/config/includes.chroot/usr/local/bin/dede-kodi-kiosk
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
mkdir -p "$tmp/runtime"
chmod 700 "$tmp/runtime"
# Simulate root even on an unprivileged CI runner: the kiosk must stop
# before profile initialization or starting Kodi/Xorg.
mkdir -p "$tmp/fake-bin"
printf '#!/bin/sh\nprintf "0\\n"\n' > "$tmp/fake-bin/id"
chmod 755 "$tmp/fake-bin/id"
if PATH="$tmp/fake-bin:$PATH" XDG_RUNTIME_DIR="$tmp/runtime" sh "$script" >"$tmp/root-output" 2>&1; then
  echo 'Kiosk accepted root execution' >&2
  exit 1
fi
grep -q 'Kodi kiosk refuses to run as root' "$tmp/root-output"
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

# Ensure skin checks remain before any profile write or Kodi/Xorg launch.
# The real skin path is image-owned and cannot be replaced in unprivileged CI.
python3 - "$script" <<'PY'
import sys
from pathlib import Path
source = Path(sys.argv[1]).read_text()
skin = source.index('skin_addon=/usr/share/kodi/addons/skin.dede-tv/addon.xml')
symlink = source.index('[ -L "$skin_addon" ]', skin)
parent = source.index('[ -L "$(dirname "$skin_addon")" ]', symlink)
profile = source.index('/usr/local/libexec/dede-kodi-profile-init', parent)
launch = source.index('exec /usr/bin/xinit', profile)
assert skin < symlink < parent < profile < launch, "Skin preflight must precede profile writes and launch"
PY

# Exercise the actual skin symlink guard, not just its source-code ordering.
# Redirect only the immutable image skin path in a temporary script copy.
# A rejected skin must never reach the profile initializer or Xorg.
python3 - "$script" "$tmp/kiosk-test" "$tmp/skin/addon.xml" <<'PY'
import sys
from pathlib import Path
source = Path(sys.argv[1]).read_text()
original = 'skin_addon=/usr/share/kodi/addons/skin.dede-tv/addon.xml'
assert source.count(original) == 1
Path(sys.argv[2]).write_text(source.replace(original, 'skin_addon=' + sys.argv[3]))
PY
# A missing or non-directory skin must fail before the profile initializer.
if XDG_RUNTIME_DIR="$tmp/runtime" sh "$tmp/kiosk-test" >"$tmp/skin-absent-output" 2>&1; then
  echo 'Kiosk accepted an absent skin' >&2
  exit 1
fi
grep -q 'Dede TV skin is missing' "$tmp/skin-absent-output"
mkdir -p "$tmp/skin"
printf 'not an addon\\n' > "$tmp/skin/addon.xml"
# An ordinary addon.xml passes this early presence check; its contents are
# validated by the profile initializer. Do not claim XML validation here.
rm "$tmp/skin/addon.xml"
rmdir "$tmp/skin"
mkdir -p "$tmp/skin" "$tmp/real-skin"
printf '<addon id="skin.dede-tv"/>\n' > "$tmp/real-skin/addon.xml"
ln -s "$tmp/real-skin/addon.xml" "$tmp/skin/addon.xml"
if XDG_RUNTIME_DIR="$tmp/runtime" sh "$tmp/kiosk-test" >"$tmp/skin-file-link-output" 2>&1; then
  echo 'Kiosk accepted a symlinked skin addon.xml' >&2
  exit 1
fi
grep -q 'Dede TV skin is missing' "$tmp/skin-file-link-output"
rm "$tmp/skin/addon.xml"
rmdir "$tmp/skin"
ln -s "$tmp/real-skin" "$tmp/skin"
if XDG_RUNTIME_DIR="$tmp/runtime" sh "$tmp/kiosk-test" >"$tmp/skin-dir-link-output" 2>&1; then
  echo 'Kiosk accepted a symlinked skin directory' >&2
  exit 1
fi
grep -q 'Dede TV skin is missing' "$tmp/skin-dir-link-output"

echo 'Kiosk fail-closed smoke checks passed'
