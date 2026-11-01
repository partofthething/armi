"""Tests that every application gets the ``gui`` entry point, and the wxPython grid editor is gone."""

import unittest

from armi import getPluginManagerOrFail
from armi.cli.gui import GuiEntryPoint


class TestGuiEntryPoint(unittest.TestCase):
    def test_everyApplicationGetsIt(self):
        offered = [each for points in getPluginManagerOrFail().hook.defineEntryPoints() for each in points]
        self.assertIn(GuiEntryPoint, offered)

    def test_theOldGridEditorIsGone(self):
        names = [each.name for points in getPluginManagerOrFail().hook.defineEntryPoints() for each in points]
        self.assertEqual(names.count("gui"), 1)
        self.assertNotIn("grids", names)

    def test_theEntryPointTakesTheOptionsItDocuments(self):
        point = GuiEntryPoint()
        point.addOptions()
        parsed = point.parser.parse_args(["case.yaml", "--port", "8123", "--no-browser"])
        self.assertEqual(parsed.input, "case.yaml")
        self.assertEqual(parsed.port, 8123)
        self.assertTrue(parsed.no_browser)
