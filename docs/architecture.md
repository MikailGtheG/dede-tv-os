# Dede TV OS — architecture decision record 0001

Status: proposed; no installable image has been built yet.

## Scope
Debian 13 minimal + Kodi fork + PVR IPTV Simple + Dede TV Core + Dede TV Skin + optional Tailscale. Do not fork the entire Debian archive. Keep security updates flowing from Debian.

## TV behavior
- Power-on: automatically resume the last selected live channel; no general desktop.
- OK: open vertically navigable channel overlay; Up/Down moves selection; OK confirms.
- Programme +/-: change channels directly.
- Custom channels: local media shuffled once per cycle, persistent wall-clock schedules without background transcoding.
- Remote administration: authenticated interface over a restricted management network only.

## Security boundaries
- Kodi runs without root; no untrusted add-ons installed.
- Kodi JSON-RPC bound to loopback or isolated Unix socket via a local controller; never reachable directly from the internet.
- Administrative UI bound to Tailscale or loopback; authentication and CSRF protections required.
- Media directories read-only to the player; web app must not accept arbitrary filesystem paths.
- SSH key authentication; deny unnecessary inbound ports.
- Explicit allow-list of Debian packages; prompt upstream security updates.
- Preserve diagnostic logs with size caps and protect secrets from logs.

## Upgrade architecture
GitHub Releases distribute versioned, signed artifacts; no private update server. Evaluate RAUC, an A/B partition layout, bootloader state transitions and watchdog-controlled rollback specifically for the target hardware. Only install a release after signature verification. Offline recovery image required.

## Pending hardware decision
CPU architecture, graphics/video decode support, HDMI-CEC input, remote key mapping, boot firmware/UEFI, disk layout and secure key provisioning are not yet specified. No update or hardening claims can be made until tested on target hardware.
