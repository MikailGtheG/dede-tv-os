#!/bin/sh
# Security regression: XML formatting must not bypass kiosk profile validation.
set -eu
init="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)/build/config/includes.chroot/usr/local/libexec/dede-kodi-profile-init"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT HUP INT TERM
export HOME="$tmp/home" DEDE_SKIN_ADDON="$tmp/skin/addon.xml"
mkdir -p "$HOME/.kodi/userdata" "$tmp/skin"
printf '%s\n' '<addon id="skin.dede-tv"/>' > "$DEDE_SKIN_ADDON"
gui="$HOME/.kodi/userdata/guisettings.xml"
reject() {
  if sh "$init"; then
    echo "FAIL: unsafe Kodi XML accepted: $1" >&2
    exit 1
  fi
}
cat > "$gui" <<'XML'
<settings><section><setting id='lookandfeel.skin'>skin.dede-tv</setting>
<setting id='services.webserver'>true</setting></section></settings>
XML
reject 'single quotes and nested webserver'
cat > "$gui" <<'XML'
<settings><setting id="lookandfeel.skin">skin.dede-tv</setting>
<setting id="lookandfeel.skin">skin.dede-tv</setting></settings>
XML
reject 'duplicate skin entries'
cat > "$gui" <<'XML'
<settings><setting id="lookandfeel.skin">skin.dede-tv</setting>
<setting id="services.esallinterfaces">
true
</setting></settings>
XML
reject 'multiline remote event server'
printf '%s\n' '<settings><setting id="lookandfeel.skin">skin.dede-tv' > "$gui"
reject 'truncated XML'
cat > "$gui" <<'XML'
<settings><section><setting id="lookandfeel.skin">skin.dede-tv</setting>
<setting id="services.webserver">false</setting>
<setting id="services.esallinterfaces">false</setting></section></settings>
XML
sh "$init"
echo 'PASS: Kodi XML security regression'
