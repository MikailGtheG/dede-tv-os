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
print("Skin marker atomic replacement checks passed")
