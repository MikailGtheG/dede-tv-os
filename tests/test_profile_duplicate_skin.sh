#!/bin/sh
# Regression: two lookandfeel.skin settings on the same line must fail closed.
set -eu
script="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)/build/config/includes.chroot/usr/local/libexec/dede-kodi-profile-init"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT HUP INT TERM
mkdir -p "$tmp/home/.kodi/userdata" "$tmp/skin"
printf '<addon id="skin.dede-tv" />\n' > "$tmp/skin/addon.xml"
printf '<settings><setting id="lookandfeel.skin">skin.dede-tv</setting><setting id="lookandfeel.skin">skin.estuary</setting></settings>\n' > "$tmp/home/.kodi/userdata/guisettings.xml"
if HOME="$tmp/home" DEDE_SKIN_ADDON="$tmp/skin/addon.xml" sh "$script"; then
  echo "FAIL: duplicate skin settings accepted" >&2
  exit 1
fi
echo "PASS: duplicate skin settings rejected"
