# Copyright 2026 Ocean Atomics, LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""Checks on the setting editors a plugin can ask for."""

import contextlib
import unittest

from armi import getPluginManagerOrFail
from armi.settings import caseSettings
from armi.webui import settingsdoc, settingseditors


class Describing:
    """A plugin that says how one framework setting should be edited."""

    editors = {
        "verbosity": {
            "widget": "choice",
            "options": ["info", "debug"],
            "group": "Testing",
            "hint": "how much it says",
        }
    }

    @staticmethod
    def defineSettingEditors(cs):
        return dict(Describing.editors)


class Broken:
    """A plugin whose hook raises."""

    @staticmethod
    def defineSettingEditors(cs):
        raise RuntimeError("no")


@contextlib.contextmanager
def registered(plugin):
    manager = getPluginManagerOrFail()
    manager.register(plugin)
    try:
        yield
    finally:
        manager.unregister(plugin)


class TestNormalize(unittest.TestCase):
    """What an editor description has to look like."""

    def test_optionsBecomeValueLabelHelp(self):
        editor = settingseditors.normalize(
            {"widget": "choice", "options": ["a", {"value": "b", "label": "Bee", "help": "why"}]}
        )
        self.assertEqual(editor["options"][0], {"value": "a", "label": "a", "help": None})
        self.assertEqual(editor["options"][1], {"value": "b", "label": "Bee", "help": "why"})

    def test_aTableDescribesItsColumns(self):
        editor = settingseditors.normalize(
            {
                "widget": "table",
                "rowLabel": "Bank",
                "columns": [
                    {"key": "direction", "widget": "choice", "options": ["top", "bottom"]},
                    {"key": "stroke", "label": "Stroke", "widget": "number", "unit": "cm"},
                ],
                "newRow": {"direction": "top"},
            }
        )
        self.assertEqual(editor["rowLabel"], "Bank")
        self.assertEqual(editor["columns"][0]["label"], "direction")
        self.assertEqual(editor["columns"][1]["unit"], "cm")
        self.assertEqual(editor["newRow"], {"direction": "top"})

    def test_thereIsNothingWrongWithNothingToChooseFrom(self):
        """A plugin offering what a core has is offering nothing until the core has one."""
        editor = settingseditors.normalize({"widget": "choices", "options": []})
        self.assertEqual(editor["options"], [])

    def test_whatItRefuses(self):
        for editor in (
            {"widget": "freehand", "options": ["a"]},
            {"widget": "choice"},
            {"widget": "table", "columns": []},
            {"widget": "table", "columns": [{"label": "no key"}]},
            {"widget": "table", "columns": [{"key": "x", "widget": "canvas"}]},
            {"widget": "choice", "options": [{"label": "no value"}]},
            "not a mapping",
        ):
            with self.assertRaises(settingseditors.EditorError, msg=str(editor)):
                settingseditors.normalize(editor)


class TestCollect(unittest.TestCase):
    """Asking the registered plugins, and surviving the answers."""

    def setUp(self):
        self.cs = caseSettings.Settings()

    def test_aPluginCanDescribeItsSettings(self):
        with registered(Describing):
            editors = settingseditors.collect(self.cs)
        self.assertEqual(editors["verbosity"]["widget"], "choice")
        self.assertEqual(editors["verbosity"]["hint"], "how much it says")

    def test_nothingIsDescribedByDefault(self):
        self.assertEqual(settingseditors.collect(self.cs), {})

    def test_aSettingThisApplicationDoesNotDefineIsIgnored(self):
        class Elsewhere:
            @staticmethod
            def defineSettingEditors(cs):
                return {"noSuchSetting": {"widget": "choice", "options": ["a"]}}

        with registered(Elsewhere):
            self.assertEqual(settingseditors.collect(self.cs), {})

    def test_aBrokenPluginDoesNotTakeTheOthersDown(self):
        with registered(Broken), registered(Describing):
            editors = settingseditors.collect(self.cs)
        self.assertIn("verbosity", editors)

    def test_anEditorThatCannotBeRenderedIsDropped(self):
        class Confused:
            @staticmethod
            def defineSettingEditors(cs):
                return {"verbosity": {"widget": "ouija"}}

        with registered(Confused):
            self.assertEqual(settingseditors.collect(self.cs), {})


class TestDescribedSettings(unittest.TestCase):
    """The editor arrives with the setting it belongs to."""

    def test_theSettingCarriesItsEditorAndMovesToItsGroup(self):
        with registered(Describing):
            described = settingsdoc.describe(caseSettings.Settings())
        entry = next(each for each in described["settings"] if each["name"] == "verbosity")
        self.assertEqual(entry["editor"]["widget"], "choice")
        self.assertEqual(entry["group"], "Testing")
        self.assertIn("Testing", described["groups"])

    def test_anUndescribedSettingSaysSo(self):
        described = settingsdoc.describe(caseSettings.Settings())
        entry = next(each for each in described["settings"] if each["name"] == "verbosity")
        self.assertIsNone(entry["editor"])
        self.assertNotEqual(entry["group"], "Testing")


if __name__ == "__main__":
    unittest.main()
