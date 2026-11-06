# Dede TV OS integration contract (alpha)

## Components pinned by the owner-controlled image workflow

- Debian 13 (Trixie) base with the packaged Kodi runtime.
- Dede TV skin compiled from a fixed reviewed Git commit and installed under `/usr/share/kodi/addons/skin.dede-tv`.
- Dede TV Rust core built from a fixed reviewed Git commit using `cargo build --release --locked` and installed as `/usr/local/bin/dede-tv-core`.
- The OS's first-boot profile initializer applies `skin.dede-tv` **only if the packaged skin add-on is present**. Otherwise it selects stock Estuary. It never overwrites existing `guisettings.xml` or `advancedsettings.xml` files.

## Playback API security

The first-boot profile enables Kodi's local application control and explicitly disables control from other machines and the HTTP web server. The raw JSON-RPC transport on TCP 9090 has **no authentication**. The Rust client is restricted to `127.0.0.1:9090` and validates a `Player.Open` acknowledgment. Do not allow a wildcard-bound (0.0.0.0) control listener or expose this port via a router. Existing profiles are deliberately not modified, and require manual validation.

The BIOS/UEFI CI boot gate checks for the first-boot GUI settings, a 127.0.0.1 listener, the absence of a non-loopback listener, and `Settings.GetSettingValue(lookandfeel.skin)` returning `skin.dede-tv`. It also checks running Xorg/Kodi processes and the captured VGA picture.

## Channel state and playback

The resume service is conditional on the Rust core binary and the presence of a saved channel database at `/home/dede/.local/share/dede-tv/channels.db`. It invokes the core's `resume-last` command, retries boundedly while Kodi initializes, and does not prevent Kodi from starting when state is absent.

Administrative provisioning after installation, with an accessible PVR channel in Kodi:

```sh
sudo -u dede mkdir -p /home/dede/.local/share/dede-tv
sudo -u dede /usr/local/bin/dede-tv-core \
  --db /home/dede/.local/share/dede-tv/channels.db select pvr:12
sudo systemctl start dede-tv-resume.service
```

`pvr:12` is **only an example**: use a real channel ID returned by the installed IPTV/PVR add-on. Initial selection of a PVR ID is syntactically validated, but successful playback additionally requires the channel to exist and be available to Kodi.

A live-build boot test is not proof of durable persistent storage across power cycles. The alpha needs an installed, persistent root/data partition, automatic IPTV configuration and real-playback/video verification before production release.

## Merge gates

1. Current trusted-branch Debian 13 ISO build and SquashFS audit pass.
2. BIOS/UEFI boot tests show Kodi active with the custom skin, and JSON-RPC accepts only local connections.
3. Core CI passes fmt/clippy/tests/smoke under `--locked`.
4. Skin packaging CI passes and a Kodi runtime skin-load test passes.
5. A supplied, authorized test stream/PVR channel plays; last-channel resume after power cycle is verified on persistent storage.
6. Kodi fork and IPTV Simple C++ builds and regressions pass. No provisional CI success is equivalent to production readiness.
