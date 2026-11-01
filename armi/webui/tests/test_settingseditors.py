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


class TestLayoutAndConditions(unittest.TestCase):
    """Ordering, section descriptions, conditions and paths."""

    def test_showWhenTakesOneValueOrSeveral(self):
        one = settingseditors.normalize({"showWhen": {"setting": "a", "equals": "USS"}})
        self.assertEqual(one["showWhen"], [{"setting": "a", "in": ["USS"]}])
        several = settingseditors.normalize({"showWhen": {"setting": "a", "in": ["x", "y"]}})
        self.assertEqual(several["showWhen"][0]["in"], ["x", "y"])
        either = settingseditors.normalize(
            {"showWhen": [{"setting": "a", "equals": "USS"}, {"setting": "b", "equals": "refined"}]}
        )
        self.assertEqual([each["setting"] for each in either["showWhen"]], ["a", "b"])
        with self.assertRaises(settingseditors.EditorError):
            settingseditors.normalize({"showWhen": []})

    def test_showWhenNeedsASettingAndOneTest(self):
        for bad in (
            {"equals": 1},
            {"setting": "a"},
            {"setting": "a", "equals": 1, "in": [1]},
            {"setting": "a", "in": "x"},
        ):
            with self.assertRaises(settingseditors.EditorError, msg=str(bad)):
                settingseditors.normalize({"showWhen": bad})

    def test_aPathKnowsWhatItPoints(self):
        self.assertEqual(settingseditors.normalize({"widget": "path"})["pathKind"], "file")
        self.assertEqual(settingseditors.normalize({"widget": "path", "pathKind": "dir"})["pathKind"], "dir")
        with self.assertRaises(settingseditors.EditorError):
            settingseditors.normalize({"widget": "path", "pathKind": "socket"})

    def test_orderMustBeANumber(self):
        self.assertEqual(settingseditors.normalize({"order": 2})["order"], 2.0)
        with self.assertRaises(settingseditors.EditorError):
            settingseditors.normalize({"order": "first"})

    def test_orderAndAGroupHintAreEnoughOnTheirOwn(self):
        self.assertEqual(settingseditors.normalize({"order": 1})["widget"], None)
        self.assertEqual(settingseditors.normalize({"groupHint": "what these do"})["groupHint"], "what these do")


class Arranging:
    """A plugin that orders two settings and describes their section."""

    @staticmethod
    def defineSettingEditors(cs):
        return {
            "verbosity": {"group": "Arranged", "order": 2, "groupHint": "Settings in a chosen order."},
            "branchVerbosity": {"group": "Arranged", "order": 1},
        }


class TestArrangedSettings(unittest.TestCase):
    def test_orderedWithinTheGroupAndDescribed(self):
        with registered(Arranging):
            described = settingsdoc.describe(caseSettings.Settings())
        arranged = [entry["name"] for entry in described["settings"] if entry["group"] == "Arranged"]
        self.assertEqual(arranged, ["branchVerbosity", "verbosity"])
        self.assertEqual(described["groupHints"]["Arranged"], "Settings in a chosen order.")


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
