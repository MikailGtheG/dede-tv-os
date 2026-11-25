#!/bin/sh
# Regression: Kodi 21 must never show its blocking "Disabled add-ons - Would
# you like to enable this add-on?" prompt for the TV skin or IPTV Simple.
# The build hook prevents it by listing both in Kodi's addon manifest.
set -eu
root=$(cd "$(dirname "$0")/.." && pwd)
hook="$root/build/config/hooks/live/0510-dede-kodi-addon-manifest.hook.chroot"
fixture="$root/tests/fixtures/kodi-21.2-addon-manifest.xml"
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT HUP INT TERM

fail() { echo "FAIL: $*" >&2; exit 1; }

count() { grep -c -- "$1" "$2" || true; }

# 1. Real Kodi 21.2-Omega manifest gains exactly the Dede entries.
cp "$fixture" "$work/m.xml"
chmod 0644 "$work/m.xml"
DEDE_KODI_MANIFEST="$work/m.xml" sh "$hook" >/dev/null
[ "$(count '<addon>skin.dede-tv</addon>' "$work/m.xml")" = 1 ] || fail "skin not required"
[ "$(count '<addon optional="true">pvr.iptvsimple</addon>' "$work/m.xml")" = 1 ] || fail "pvr not optional"
for lang in de_de tr_tr; do
  [ "$(count "<addon optional=\"true\">resource.language.$lang</addon>" "$work/m.xml")" = 1 ] || fail "language $lang not enabled"
done
[ "$(count '<addon>plugin.video.dede-virtual</addon>' "$work/m.xml")" = 1 ] || fail "virtual channel plugin not required"
[ "$(count '<addon>service.dede-setup</addon>' "$work/m.xml")" = 1 ] || fail "setup service not required"
# Upstream entries (Estuary stays required for Kodi itself) are preserved.
[ "$(count '<addon>skin.estuary</addon>' "$work/m.xml")" = 1 ] || fail "estuary removed"
[ "$(count '<addon optional="true">inputstream.adaptive</addon>' "$work/m.xml")" = 1 ] || fail "inputstream.adaptive changed"
[ "$(grep -c '<addon' "$work/m.xml")" = "$(( $(grep -c '<addon' "$fixture") + 6 ))" ] || fail "unexpected entry count"
[ "$(stat -c %a "$work/m.xml")" = 644 ] || fail "manifest mode changed"
python3 -c 'import sys, xml.etree.ElementTree as ET; ET.parse(sys.argv[1])' "$work/m.xml" || fail "invalid XML"

# 2. Idempotent: a second run changes nothing.
cp "$work/m.xml" "$work/before.xml"
DEDE_KODI_MANIFEST="$work/m.xml" sh "$hook" >/dev/null
cmp -s "$work/before.xml" "$work/m.xml" || fail "hook is not idempotent"

# 3. An optional skin entry would let an administrator UI disable the TV skin.
sed 's#</addons>#  <addon optional="true">skin.dede-tv</addon>\n</addons>#' "$fixture" > "$work/opt.xml"
if DEDE_KODI_MANIFEST="$work/opt.xml" sh "$hook" >/dev/null 2>&1; then
  fail "optional TV skin entry accepted"
fi

# 4. Hostile or broken manifests fail the image build.
printf '<!DOCTYPE x [<!ENTITY a "b">]><addons/>' > "$work/dtd.xml"
if DEDE_KODI_MANIFEST="$work/dtd.xml" sh "$hook" >/dev/null 2>&1; then fail "DTD accepted"; fi
printf '<settings/>' > "$work/root.xml"
if DEDE_KODI_MANIFEST="$work/root.xml" sh "$hook" >/dev/null 2>&1; then fail "wrong root accepted"; fi
ln -s "$fixture" "$work/link.xml"
if DEDE_KODI_MANIFEST="$work/link.xml" sh "$hook" >/dev/null 2>&1; then fail "symlink accepted"; fi
if DEDE_KODI_MANIFEST="$work/missing.xml" sh "$hook" >/dev/null 2>&1; then fail "missing manifest accepted"; fi
cmp -s "$fixture" "$root/tests/fixtures/kodi-21.2-addon-manifest.xml" || fail "fixture modified"

echo "Kodi addon manifest hook checks passed"
