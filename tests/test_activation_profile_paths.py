#!/usr/bin/env python3
"""Regression: activation must not follow redirected Kodi profile parents."""
import importlib.machinery
import importlib.util
import tempfile
from pathlib import Path
from unittest import TestCase, main
from unittest.mock import patch

SCRIPT = (Path(__file__).resolve().parents[1] /
          "build/config/includes.chroot/usr/local/libexec/dede-kodi-activate-skin")
loader = importlib.machinery.SourceFileLoader("activation_paths", str(SCRIPT))
spec = importlib.util.spec_from_loader(loader.name, loader)
activation = importlib.util.module_from_spec(spec)
loader.exec_module(activation)


class ActivationPathTests(TestCase):
    def test_redirected_userdata_blocks_skin_and_first_boot_marker(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            actual = root / "actual"
            actual.mkdir()
            (actual / "guisettings.xml").write_text(
                '<settings><setting id="lookandfeel.skin">skin.dede-tv</setting></settings>',
                encoding="utf-8")
            (actual / ".dede-skin-first-boot-pending").write_text(
                "skin.dede-tv\n", encoding="ascii")
            redirected = root / "userdata"
            redirected.symlink_to(actual, target_is_directory=True)
            with patch.object(activation, "SETTINGS", redirected / "guisettings.xml"), \
                 patch.object(activation, "PENDING", redirected / ".dede-skin-first-boot-pending"):
                self.assertIsNone(activation.desired_skin())
                self.assertFalse(activation.first_boot_pending())

    def test_oversized_first_boot_marker_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            pending = Path(directory) / ".dede-skin-first-boot-pending"
            pending.write_bytes(b"skin.dede-tv\n" + b"x" * (1024 * 1024))
            with patch.object(activation, "PENDING", pending):
                self.assertFalse(activation.first_boot_pending())

    def test_first_boot_marker_requires_exact_bytes(self):
        # A valid prefix is insufficient: reject truncation, CRLF and padding.
        invalid = (
            b"skin.dede-tv",
            b"skin.dede-tv\\r\\n",
            b"skin.dede-tv\\n\\n",
            b"skin.dede-tv\\n ",
            b" skin.dede-tv\\n",
            b"skin.dede-tv\\x00\\n",
        )
        with tempfile.TemporaryDirectory() as directory:
            pending = Path(directory) / ".dede-skin-first-boot-pending"
            with patch.object(activation, "PENDING", pending):
                for payload in invalid:
                    with self.subTest(payload=payload):
                        pending.write_bytes(payload)
                        self.assertFalse(activation.first_boot_pending())

    def test_symlinked_first_boot_marker_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            actual = root / "actual-marker"
            actual.write_bytes(b"skin.dede-tv\\n")
            pending = root / ".dede-skin-first-boot-pending"
            pending.symlink_to(actual)
            with patch.object(activation, "PENDING", pending):
                self.assertFalse(activation.first_boot_pending())
            self.assertEqual(actual.read_bytes(), b"skin.dede-tv\\n")

    def test_invalid_marker_encoding_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            pending = Path(directory) / ".dede-skin-first-boot-pending"
            pending.write_bytes(b"skin.dede-tv\xff")
            with patch.object(activation, "PENDING", pending):
                self.assertFalse(activation.first_boot_pending())

    def test_utf16_entity_declaration_is_rejected_before_parsing(self):
        # A byte search for <!ENTITY misses UTF-16 encoded declarations.
        payload = ('<?xml version="1.0" encoding="UTF-16"?>'
                   '<!DOCTYPE settings [<!ENTITY secret "unsafe">]>'
                   '<settings><setting id="lookandfeel.skin">&secret;</setting></settings>')
        with tempfile.TemporaryDirectory() as directory:
            settings = Path(directory) / "guisettings.xml"
            settings.write_bytes(payload.encode("utf-16"))
            with patch.object(activation, "SETTINGS", settings):
                with self.assertRaisesRegex(ValueError, "UTF-8"):
                    activation.desired_skin()

    def test_regular_profile_files_remain_valid(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            settings = root / "guisettings.xml"
            pending = root / ".dede-skin-first-boot-pending"
            settings.write_text(
                '<settings><setting id="lookandfeel.skin">skin.dede-tv</setting></settings>',
                encoding="utf-8")
            pending.write_text("skin.dede-tv\n", encoding="ascii")
            with patch.object(activation, "SETTINGS", settings), \
                 patch.object(activation, "PENDING", pending):
                self.assertEqual(activation.desired_skin(), "skin.dede-tv")
                self.assertTrue(activation.first_boot_pending())


if __name__ == "__main__":
    main()
