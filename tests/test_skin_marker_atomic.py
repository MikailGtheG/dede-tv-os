#!/usr/bin/env python3
"""Regression: Kodi activation markers must never follow stale symlinks."""
import importlib.util
from importlib.machinery import SourceFileLoader
import os
from pathlib import Path
import stat
import tempfile

script = Path("build/config/includes.chroot/usr/local/libexec/dede-kodi-activate-skin")
spec = importlib.util.spec_from_loader(
    "dede_skin_activation", SourceFileLoader("dede_skin_activation", str(script))
)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

with tempfile.TemporaryDirectory() as directory:
    root = Path(directory)
    module.PROFILE = root
    module.MARKER = root / ".dede-skin-activated"
    victim = root / "unrelated-settings"
    victim.write_text("preserve me\n", encoding="ascii")
    module.MARKER.symlink_to(victim)
    module.write_activation_marker()
    assert victim.read_text(encoding="ascii") == "preserve me\n"
    assert not module.MARKER.is_symlink()
    assert module.MARKER.read_text(encoding="ascii") == "skin.dede-tv\n"
    assert stat.S_IMODE(module.MARKER.stat().st_mode) == 0o600
    module.write_activation_marker()
    assert module.MARKER.read_text(encoding="ascii") == "skin.dede-tv\n"
    assert sorted(path.name for path in root.iterdir()) == [
        ".dede-skin-activated", "unrelated-settings"
    ]

# The marker can be relocated independently of PROFILE. Temporary files must
# be created next to the target so os.replace remains atomic across mounts.
with tempfile.TemporaryDirectory() as directory:
    root = Path(directory)
    profile = root / "profile"
    target = root / "separate-target"
    profile.mkdir()
    target.mkdir()
    module.PROFILE = profile
    module.MARKER = target / ".dede-skin-activated"
    module.write_activation_marker()
    assert module.MARKER.read_text(encoding="ascii") == "skin.dede-tv\n"
    assert stat.S_IMODE(module.MARKER.stat().st_mode) == 0o600
    assert list(profile.iterdir()) == []
    assert sorted(p.name for p in target.iterdir()) == [".dede-skin-activated"]


# The activation gate must reject duplicate lookandfeel.skin settings, even
# when one entry requests Dede TV. Kodi's nested setting layout is supported.
with tempfile.TemporaryDirectory() as directory:
    root = Path(directory)
    module.SETTINGS = root / "guisettings.xml"
    module.SETTINGS.write_text(
        '<settings><section><setting id="lookandfeel.skin">skin.dede-tv</setting>'
        '<setting id="lookandfeel.skin">skin.estuary</setting></section></settings>',
        encoding="utf-8",
    )
    assert module.desired_skin() is None
    module.SETTINGS.write_text(
        '<settings><section><setting id="lookandfeel.skin">skin.estuary</setting>'
        '<setting id="lookandfeel.skin">skin.dede-tv</setting></section></settings>',
        encoding="utf-8",
    )
    assert module.desired_skin() is None
    module.SETTINGS.write_text(
        '<settings><section><setting id="lookandfeel.skin">skin.dede-tv</setting>'
        '</section></settings>',
        encoding="utf-8",
    )
    assert module.desired_skin() == "skin.dede-tv"
    module.SETTINGS.write_text(
        '<settings><setting id="lookandfeel.skin">skin.dede-tv</setting>'
        '<setting id="lookandfeel.skin">skin.dede-tv</setting></settings>',
        encoding="utf-8",
    )
    assert module.desired_skin() is None


# The activation gate must enforce the same bounded, DTD-free XML contract
# as the standalone profile initializer.
with tempfile.TemporaryDirectory() as directory:
    module.SETTINGS = Path(directory) / "guisettings.xml"
    valid = '<settings><setting id="lookandfeel.skin">skin.dede-tv</setting></settings>'
    module.SETTINGS.write_text(valid, encoding="utf-8")
    assert module.desired_skin() == "skin.dede-tv"
    module.SETTINGS.write_text('<!DOCTYPE settings>' + valid, encoding="utf-8")
    try:
        module.desired_skin()
        raise AssertionError("DTD must be rejected")
    except ValueError:
        pass
    module.SETTINGS.write_text('<other>' + valid + '</other>', encoding="utf-8")
    try:
        module.desired_skin()
        raise AssertionError("unexpected XML root must be rejected")
    except ValueError:
        pass
    module.SETTINGS.write_bytes(b" " * (1024 * 1024 + 1))
    try:
        module.desired_skin()
        raise AssertionError("oversized XML must be rejected")
    except ValueError:
        pass

print("Skin marker and unique skin preference checks passed")
