# Dede TV OS integration contract (alpha)

## Components pinned by the owner-controlled image workflow

- Debian 13 (Trixie) base with the packaged Kodi runtime.
- Dede TV skin compiled from a fixed reviewed Git commit and installed under `/usr/share/kodi/addons/skin.dede-tv`.
- Dede TV Rust core built from a fixed reviewed Git commit using `cargo build --release --locked` and installed as `/usr/local/bin/dede-tv-core`.
- The OS first-boot profile initializer requires valid packaged `skin.dede-tv` metadata and configures that skin. It fails closed rather than silently choosing stock Estuary. Existing administrator-managed settings remain untouched and are security-validated.

## Playback API security

The first-boot profile enables Kodi's local application control and explicitly disables control from other machines and the HTTP web server. The raw JSON-RPC transport on TCP 9090 has **no authentication**. The Rust client is restricted to `127.0.0.1:9090` and validates a `Player.Open` acknowledgment. Do not allow a wildcard-bound (0.0.0.0) control listener or expose this port via a router. Existing profiles are deliberately not modified, and require manual validation.

The BIOS/UEFI CI boot gate verifies the loopback-only listener, `Settings.GetSettingValue(lookandfeel.skin)`, Xorg/Kodi process presence, non-blank QEMU VGA capture, and now **rejects the known stock Kodi branding/disabled-IPTV-modal visual signatures**. A `lookandfeel.skin` setting alone is **not** a proof that the rendered GUI is Dede TV. Successful device playback and CEC remote integration require separate runtime evidence.

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

## Merge gates (2026-11-08)

Keep all dependent PRs as drafts until the following are demonstrably satisfied:

| Gate | Status | Objective evidence |
| --- | --- | --- |
| Pinned Debian 13 + Rust core + Kodi 21.2-compatible skin in one image | Implemented | `.github/workflows/os-image.yml` SHA pins and SquashFS audit |
| Core unit tests, skin package tests and OS static CI | Passed on current component heads | Respective GitHub Actions runs |
| First boot does not show Kodi / IPTV Simple activation modal | **Blocked pending new QEMU screenshot** | Both BIOS and UEFI captures must pass `tests/check_qemu_tv_visual.py` |
| Actual Dede Home rendered, not merely selected as skin setting | **Blocked** | Visual review and full first-start log |
| Authorized playlist/test media and fullscreen playback | **Blocked** | Real running video, `Player.Open` response and screenshot |
| OK/Back and channel +/- from a TV remote | **Blocked** | Recorded hardware/CEC remote end-to-end test |
| Resume after reboot with persistent channel database | **Blocked** | Installed-system reboot validation |
| Kodi fork native compile and integration | **Blocked** | Its own CI; do not substitute Debian Kodi for a fork acceptance gate |
| Security and recovery before merge | In progress | No remote JSON-RPC, bounded retries, fail-closed profile |

The 2026-11-08 successful ISO run `42466142644` still displayed stock
Kodi and the IPTV Simple confirmation dialog in its actual BIOS screenshot.
It is not evidence of completed TV UI integration. The visual CI rule was
subsequently strengthened and the first-boot PVR add-on enable flow changed.
A newer full ISO run must prove this actually fixes the problem.

### Merge policy

- Preserve the currently working draft branches; do not merge a repo just
  because an unrelated static build is green.
- Favor one pinned integration candidate per ISO test; avoid repeated
  cancellation of expensive running QEMU builds.
- Capture and retain both BIOS and UEFI real screenshots with every release
  candidate. If the image still contains the Kodi UI, fail the release gate.
- Keep administrator provisioning (credentials, playlist URLs, Tailscale
  configuration) outside hardcoded images and test fixtures.
