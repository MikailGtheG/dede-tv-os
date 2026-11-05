# Debian 13 prototype ISO builder

This is a **first bootable-live-image build configuration**, not a finished
Dede TV OS or automatically updating installer.

## Supported build environment

Use a **Debian 13 amd64 host** or isolated Debian 13 build environment with
live-build, debootstrap, xorriso, squashfs-tools, grub-pc-bin, grub-efi-amd64-bin.
Run as root in a disposable build runner; the tool performs chroot operations.

```sh
sudo apt-get update
sudo apt-get install -y --no-install-recommends \
  live-build debootstrap xorriso squashfs-tools \
  grub-pc-bin grub-efi-amd64-bin
cd build
sudo lb config
sudo lb build
```

The generated ISO is usually `live-image-amd64.hybrid.iso`. Validate
boot, codecs, audio, remote control and networking in a VM **before** using
real TV hardware. No auto-install, disk partition writes or signed updates.

## Current limitations and security boundary

- The baseline uses Debian's maintained Kodi and PVR packages, **not**
  our patched Kodi forks yet.
- No auto-start into Kodi: a full remote-control and GPU/DRM environment
  depends on selecting actual target hardware.
- Not hardened and not zero-trust out of the box; a live ISO is a build smoke
  test, not a deployable appliance.
- No RAUC partition table or A/B rollback yet.
- Internet access and Debian mirrors are required at build time.
- Debian package versions aren't pinned to immutable snapshots, so this is
  a repeatable build *procedure*, not a bit-for-bit reproducible ISO.

## Next release gates

1. Boot ISO under QEMU (UEFI and BIOS) and confirm package manifest.
2. Build and package our C++ Kodi/PVR forks, pin commit SHAs and signatures.
3. Integrate the Rust Core binary and custom skin after cross-repo CI.
4. Validate HDMI-CEC, hardware acceleration, Turkish fonts and boot-to-last-channel.
5. Design installed A/B image + RAUC signing/rollback separately.
