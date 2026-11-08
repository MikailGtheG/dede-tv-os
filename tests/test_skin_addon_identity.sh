#!/bin/sh
# The skin identity must be the root addon ID, not a matching comment or nested element.
set -eu
init="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)/build/config/includes.chroot/usr/local/libexec/dede-kodi-profile-init"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT HUP INT TERM
export HOME="$tmp/home" DEDE_SKIN_ADDON="$tmp/addon.xml"
mkdir -p "$HOME"
reject() {
  if sh "$init"; then
    echo "FAIL: accepted invalid skin addon metadata: $1" >&2
    exit 1
  fi
  test ! -e "$HOME/.kodi/userdata/guisettings.xml"
  test ! -e "$HOME/.kodi/userdata/advancedsettings.xml"
}
printf '%s\n' '<!-- id="skin.dede-tv" --><addon id="skin.estuary"/>' > "$DEDE_SKIN_ADDON"
reject 'matching ID in comment'
printf '%s\n' '<addon id="skin.estuary"><extension id="skin.dede-tv"/></addon>' > "$DEDE_SKIN_ADDON"
reject 'matching ID in nested element'
printf '%s\n' '<addon id="skin.dede-tv"' > "$DEDE_SKIN_ADDON"
reject 'truncated addon XML'
# XML attribute quoting is not semantically significant. The kiosk must not
# reject metadata already accepted by the strict XML identity parser.
printf '%s\n' "<addon id='skin.dede-tv'/>" > "$DEDE_SKIN_ADDON"
sh "$init"
test -s "$HOME/.kodi/userdata/guisettings.xml"
rm "$HOME/.kodi/userdata/guisettings.xml"
printf '%s\n' '<addon id="skin.dede-tv"/>' > "$DEDE_SKIN_ADDON"
sh "$init"
test -s "$HOME/.kodi/userdata/guisettings.xml"
# Invalid addon metadata must also fail closed after successful provisioning,
# without modifying the existing administrator-managed profile.
cp "$HOME/.kodi/userdata/guisettings.xml" "$tmp/saved-gui"
cp "$HOME/.kodi/userdata/advancedsettings.xml" "$tmp/saved-advanced"
printf '%s\n' '<addon id="skin.estuary"/>' > "$DEDE_SKIN_ADDON"
if sh "$init"; then
  echo 'FAIL: accepted replaced skin metadata on subsequent boot' >&2
  exit 1
fi
cmp "$tmp/saved-gui" "$HOME/.kodi/userdata/guisettings.xml"
cmp "$tmp/saved-advanced" "$HOME/.kodi/userdata/advancedsettings.xml"
echo 'PASS: Kodi skin addon identity validation'
