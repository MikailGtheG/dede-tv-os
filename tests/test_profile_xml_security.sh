#!/bin/sh
# Security regression: XML formatting must not bypass kiosk profile validation.
set -eu
init="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)/build/config/includes.chroot/usr/local/libexec/dede-kodi-profile-init"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT HUP INT TERM
export HOME="$tmp/home" DEDE_SKIN_ADDON="$tmp/skin/addon.xml"
mkdir -p "$HOME/.kodi/userdata" "$tmp/skin"
# A redirected HOME must not be used to create Kodi profile files.
original_home="$HOME"
mkdir -p "$tmp/redirected-home"
ln -s "$tmp/redirected-home" "$tmp/home-link"
if HOME="$tmp/home-link" sh "$init" >"$tmp/home-symlink-output" 2>&1; then
  echo 'FAIL: symlinked Kodi HOME accepted' >&2
  exit 1
fi
grep -q 'Unsafe Kodi HOME directory' "$tmp/home-symlink-output"
test ! -e "$tmp/redirected-home/.kodi"
# A non-directory HOME must also fail before any profile writes.
printf '%s\n' 'not a directory' > "$tmp/home-file"
if HOME="$tmp/home-file" sh "$init" >"$tmp/home-file-output" 2>&1; then
  echo 'FAIL: non-directory Kodi HOME accepted' >&2
  exit 1
fi
grep -q 'Unsafe Kodi HOME directory' "$tmp/home-file-output"
export HOME="$original_home"
# Shared HOME lets other users replace Kodi profile content before startup.
chmod 777 "$HOME"
if sh "$init" >"$tmp/home-permissions-output" 2>&1; then
  echo 'FAIL: world-writable Kodi HOME accepted' >&2
  exit 1
fi
grep -q 'Unsafe Kodi HOME ownership or permissions' "$tmp/home-permissions-output"
test ! -e "$HOME/.kodi/userdata/advancedsettings.xml"
chmod 755 "$HOME"
# Both existing Kodi profile directories must be private, not merely HOME.
for dir in "$HOME/.kodi" "$HOME/.kodi/userdata"; do
  chmod 777 "$dir"
  if sh "$init" >"$tmp/profile-permissions-output" 2>&1; then
    echo "FAIL: world-writable Kodi profile directory accepted: $dir" >&2
    exit 1
  fi
  grep -q 'Unsafe Kodi profile directory ownership or permissions' "$tmp/profile-permissions-output"
  test ! -e "$HOME/.kodi/userdata/advancedsettings.xml"
  chmod 755 "$dir"
done
printf '%s\n' '<addon id="skin.dede-tv"/>' > "$DEDE_SKIN_ADDON"
# The addon manifest must be a regular file at the packaged path, not a redirect.
manifest="$DEDE_SKIN_ADDON"
mv "$manifest" "$tmp/skin/real-addon.xml"
ln -s "$tmp/skin/real-addon.xml" "$manifest"
if sh "$init" >"$tmp/skin-link-output" 2>&1; then
  echo 'FAIL: symlinked Dede TV addon metadata accepted' >&2
  exit 1
fi
grep -q 'Dede TV skin metadata missing or invalid' "$tmp/skin-link-output"
test ! -e "$HOME/.kodi/userdata/advancedsettings.xml"
rm "$manifest"
mv "$tmp/skin/real-addon.xml" "$manifest"
# Reject a redirected addon directory even when addon.xml itself is regular.
mv "$tmp/skin" "$tmp/real-skin"
ln -s "$tmp/real-skin" "$tmp/skin"
if sh "$init" >"$tmp/skin-dir-link-output" 2>&1; then
  echo 'FAIL: symlinked Dede TV skin directory accepted' >&2
  exit 1
fi
grep -q 'Dede TV skin metadata missing or invalid' "$tmp/skin-dir-link-output"
test ! -e "$HOME/.kodi/userdata/advancedsettings.xml"
rm "$tmp/skin"
mv "$tmp/real-skin" "$tmp/skin"
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
# Rejected profiles must not cause partial first-boot provisioning.
if [ -e "$HOME/.kodi/userdata/advancedsettings.xml" ]; then
  echo 'FAIL: unsafe GUI profile caused advancedsettings.xml to be written' >&2
  exit 1
fi
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
# A valid skin selection must not mask duplicate or missing network controls.
for key in services.webserver services.esallinterfaces; do
  cat > "$gui" <<XML
<settings><setting id="lookandfeel.skin">skin.dede-tv</setting>
<setting id="services.webserver">false</setting>
<setting id="services.esallinterfaces">false</setting>
<setting id="$key">true</setting></settings>
XML
  reject "duplicate network-control setting: $key"
done
cat > "$gui" <<'XML'
<settings><setting id="lookandfeel.skin">skin.dede-tv</setting>
<setting id="services.webserver">false</setting></settings>
XML
reject 'missing explicit event server network restriction'
cat > "$gui" <<'XML'
<settings><setting id="lookandfeel.skin">skin.dede-tv</setting>
<setting id="services.esallinterfaces">false</setting></settings>
XML
reject 'missing explicit webserver restriction'
test ! -e "$HOME/.kodi/userdata/advancedsettings.xml"
printf '%s\n' '<settings><setting id="lookandfeel.skin">skin.dede-tv' > "$gui"
reject 'truncated XML'
cat > "$gui" <<'XML'
<settings><section><setting id="lookandfeel.skin">skin.dede-tv</setting>
<setting id="services.esenabled">true</setting>
<setting id="services.webserver">false</setting>
<setting id="services.esallinterfaces">false</setting></section></settings>
XML
# The remote must retain the local Kodi event server after reboot.
for value in false missing; do
  if [ "$value" = missing ]; then
    sed '/id="services.esenabled"/d' "$gui" > "$tmp/gui-new.xml"
  else
    sed 's/id="services.esenabled">true/id="services.esenabled">false/' "$gui" > "$tmp/gui-new.xml"
  fi
  mv "$tmp/gui-new.xml" "$gui"
  reject "local Kodi event server $value"
  cat > "$gui" <<'XML'
<settings><section><setting id="lookandfeel.skin">skin.dede-tv</setting>
<setting id="services.esenabled">true</setting>
<setting id="services.webserver">false</setting>
<setting id="services.esallinterfaces">false</setting></section></settings>
XML
done
# DTD and entity declarations must never be processed from persisted profiles.
cat > "$gui" <<'XML'
<!DOCTYPE settings [<!ENTITY injected "skin.dede-tv">]>
<settings><setting id="lookandfeel.skin">&injected;</setting>
<setting id="services.esenabled">true</setting>
<setting id="services.webserver">false</setting>
<setting id="services.esallinterfaces">false</setting></settings>
XML
reject 'DTD in GUI settings'
cat > "$gui" <<'XML'
<settings><setting id="lookandfeel.skin">skin.dede-tv</setting>
<setting id="services.esenabled">true</setting>
<setting id="services.webserver">false</setting>
<setting id="services.esallinterfaces">false</setting></settings>
XML
advanced="$HOME/.kodi/userdata/advancedsettings.xml"
cat > "$advanced" <<'XML'
<!DOCTYPE advancedsettings [<!ENTITY injected "true">]>
<advancedsettings><fullscreen>&injected;</fullscreen></advancedsettings>
XML
reject 'DTD in advanced settings'
rm "$advanced"
# A symlink or directory at advancedsettings.xml must fail closed.
outside="$tmp/outside.xml"
printf '%s\n' '<advancedsettings/>' > "$outside"
ln -s "$outside" "$HOME/.kodi/userdata/advancedsettings.xml"
reject 'symlinked advancedsettings'
test "$(cat "$outside")" = '<advancedsettings/>'
rm "$HOME/.kodi/userdata/advancedsettings.xml"
mkdir "$HOME/.kodi/userdata/advancedsettings.xml"
reject 'directory at advancedsettings path'
rmdir "$HOME/.kodi/userdata/advancedsettings.xml"
# Invalid administrator-managed advancedsettings must fail before Kodi starts.
advanced="$HOME/.kodi/userdata/advancedsettings.xml"
printf '%s\n' '<advancedsettings><broken>' > "$advanced"
reject 'truncated advancedsettings XML'
test "$(cat "$advanced")" = '<advancedsettings><broken>'
printf '%s\n' '<settings/>' > "$advanced"
reject 'unexpected advancedsettings root'
printf '%s\n' '<advancedsettings><fullscreen>true</fullscreen></advancedsettings>' > "$advanced"
sh "$init"
test "$(cat "$advanced")" = '<advancedsettings><fullscreen>true</fullscreen></advancedsettings>'
echo 'PASS: Kodi XML security regression'
