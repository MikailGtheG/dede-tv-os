#!/usr/bin/env python3
"""dede-ui-language: Kodi's interface language follows the console's choice."""
import importlib.machinery
import importlib.util
from pathlib import Path
import tempfile
import unittest
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parent.parent
TOOL = ROOT / "build/config/includes.chroot/usr/local/libexec/dede-ui-language"
loader = importlib.machinery.SourceFileLoader("dede_ui_language", str(TOOL))
spec = importlib.util.spec_from_loader(loader.name, loader)
language = importlib.util.module_from_spec(spec)
loader.exec_module(language)


class UiLanguageTests(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.settings = self.dir / "guisettings.xml"
        self.settings.write_text('<settings version="2">\n  <setting id="lookandfeel.skin">skin.dede-tv</setting>\n'
                                 '  <setting id="locale.language" default="true">resource.language.en_gb</setting>\n'
                                 '</settings>\n')

    def value(self):
        root = ET.parse(self.settings).getroot()
        node = [s for s in root.iter("setting") if s.get("id") == "locale.language"]
        self.assertEqual(len(node), 1)
        return node[0].text, node[0].get("default")

    def test_default_is_german(self):
        self.assertEqual(language.configured(self.dir / "missing.conf"), "de")
        self.assertTrue(language.apply("de", self.settings))
        self.assertEqual(self.value(), ("resource.language.de_de", None))
        self.assertFalse(language.apply("de", self.settings), "unchanged settings are not rewritten")

    def test_config_values(self):
        conf = self.dir / "ui.conf"
        for text, expected in (("LANGUAGE=tr\n", "tr"), ("# x\nLANGUAGE=en\n", "en"),
                               ("LANGUAGE=fr\n", "de"), ("LANGUAGE=../../x\n", "de")):
            conf.write_text(text)
            self.assertEqual(language.configured(conf), expected)

    def test_missing_setting_is_added_and_others_kept(self):
        self.settings.write_text('<settings version="2"><setting id="lookandfeel.skin">skin.dede-tv</setting></settings>')
        self.assertTrue(language.apply("tr", self.settings))
        self.assertEqual(self.value(), ("resource.language.tr_tr", None))
        self.assertIn("skin.dede-tv", self.settings.read_text())

    def test_symlinked_or_missing_settings_are_left_alone(self):
        target = self.dir / "elsewhere.xml"
        target.write_text(self.settings.read_text())
        link = self.dir / "link.xml"
        link.symlink_to(target)
        self.assertFalse(language.apply("tr", link))
        self.assertNotIn("tr_tr", target.read_text())
        self.assertFalse(language.apply("tr", self.dir / "none.xml"))


if __name__ == "__main__":
    unittest.main()
