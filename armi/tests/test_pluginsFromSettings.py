"""Tests for adding plugins to an App from settings files or ``ARMI_PLUGINS``, without writing a new App."""

import os
import textwrap
import unittest
from unittest import mock

import armi
from armi import apps, getApp, plugins, settings
from armi.settings import caseSettings
from armi.utils import directoryChangers

THIS_MODULE = "armi.tests.test_pluginsFromSettings"


class ExtraSettingPlugin(plugins.ArmiPlugin):
    """A plugin that defines its own setting, which has to be known before the settings file is read."""

    @staticmethod
    @plugins.HOOKIMPL
    def defineSettings():
        return [settings.setting.Setting("extraPluginKnob", default=1.0, label="knob", description="knob")]


class OtherPlugin(plugins.ArmiPlugin):
    """A second, empty plugin."""


NOT_A_PLUGIN = 42

PLUGIN_FILE = """
from armi import plugins

class FilePlugin(plugins.ArmiPlugin):
    pass
"""


def _writeSettingsFile(path, pluginPaths):
    lines = "\n".join(f"    - {p}" for p in pluginPaths)
    with open(path, "w") as f:
        f.write(f"settings:\n  plugins:\n{lines}\n  extraPluginKnob: 3.0\n")


class TestImportPlugin(unittest.TestCase):
    def test_importDotted(self):
        self.assertIs(apps.importPlugin(f"{THIS_MODULE}.ExtraSettingPlugin"), ExtraSettingPlugin)

    def test_importColon(self):
        self.assertIs(apps.importPlugin(f" {THIS_MODULE}:OtherPlugin "), OtherPlugin)

    def test_importFromFile(self):
        with directoryChangers.TemporaryDirectoryChanger():
            with open("filePlugin.py", "w") as f:
                f.write(PLUGIN_FILE)
            plugin = apps.importPlugin(os.path.abspath("filePlugin.py") + ":FilePlugin")
        self.assertEqual(plugin.__name__, "FilePlugin")
        self.assertTrue(issubclass(plugin, plugins.ArmiPlugin))

    def test_importNonPlugin(self):
        with self.assertRaises(TypeError):
            apps.importPlugin(f"{THIS_MODULE}.NOT_A_PLUGIN")


class TestFindRequestedPlugins(unittest.TestCase):
    def setUp(self):
        self.td = directoryChangers.TemporaryDirectoryChanger()
        self.td.__enter__()

    def tearDown(self):
        self.td.__exit__(None, None, None)

    def test_readPluginsFromSettingsFile(self):
        _writeSettingsFile("case.yaml", ["a.B", "c.D"])
        self.assertEqual(apps.readPluginsFromSettingsFile("case.yaml"), ["a.B", "c.D"])

    def test_readPluginsFromNonSettingsFile(self):
        with open("bp.yaml", "w") as f:
            f.write("blocks: {}\n")
        self.assertEqual(apps.readPluginsFromSettingsFile("bp.yaml"), [])

    def test_findFromArgsAndEnv(self):
        _writeSettingsFile("case.yaml", ["a.B", "c.D"])
        with open("broken.yaml", "w") as f:
            f.write("settings: [\n")
        args = ["run", "case.yaml", "broken.yaml", "missing.yaml", "--verbosity=debug"]
        with mock.patch.dict(os.environ, {apps.PLUGINS_ENV_VAR: "x.Y, a.B,"}):
            self.assertEqual(apps.findRequestedPlugins(args), ["x.Y", "a.B", "c.D"])

    def test_findNothing(self):
        with mock.patch.dict(os.environ, {apps.PLUGINS_ENV_VAR: ""}):
            self.assertEqual(apps.findRequestedPlugins(["run"]), [])


class TestConfigureWithPlugins(unittest.TestCase):
    def _configure(self, app, **kwargs):
        """Run ``armi.configure`` on a fresh App without disturbing the App the test session is configured with."""
        with (
            mock.patch.object(armi, "_app", None),
            mock.patch.object(armi.context, "BLUEPRINTS_IMPORTED", False),
            mock.patch.object(armi, "_liveInterpreter", return_value=False),
            mock.patch("armi.reactor.parameters.collectPluginParameters"),
            mock.patch("armi.reactor.parameters.applyAllParameters"),
            mock.patch.object(app, "registerPluginFlags"),
        ):
            armi.configure(app, **kwargs)

    def test_configureAddsRequestedPlugins(self):
        """Plugins named in a settings file or ARMI_PLUGINS are registered, along with the settings they define."""
        app = apps.App()
        with directoryChangers.TemporaryDirectoryChanger():
            _writeSettingsFile("case.yaml", [f"{THIS_MODULE}.ExtraSettingPlugin"])
            with mock.patch.dict(os.environ, {apps.PLUGINS_ENV_VAR: f"{THIS_MODULE}:OtherPlugin"}):
                self._configure(app, settingsFiles=["case.yaml"])

        self.assertTrue(app.pluginManager.has_plugin("ExtraSettingPlugin"))
        self.assertTrue(app.pluginManager.has_plugin("OtherPlugin"))
        self.assertTrue(app.pluginManager.has_plugin("NeutronicsPlugin"))
        self.assertIn("extraPluginKnob", app.getSettings())

    def test_configureReadsCommandLine(self):
        """By default, the settings files come from the command line, for any App."""
        app = apps.App()
        with directoryChangers.TemporaryDirectoryChanger():
            _writeSettingsFile("case.yaml", [f"{THIS_MODULE}.OtherPlugin"])
            with mock.patch.object(armi.sys, "argv", ["myapp", "run", "case.yaml"]):
                self._configure(app)
        self.assertTrue(app.pluginManager.has_plugin("OtherPlugin"))

    def test_registerPluginsTwice(self):
        app = apps.App()
        app.registerPlugins([f"{THIS_MODULE}.OtherPlugin"])
        app.registerPlugins([f"{THIS_MODULE}.OtherPlugin", f"{THIS_MODULE}:OtherPlugin"])
        self.assertEqual(sum(name == "OtherPlugin" for name, _ in app.pluginManager.list_name_plugin()), 1)

    def test_registerPluginsAfterConfigure(self):
        with self.assertRaises(RuntimeError):
            getApp().registerPlugins([f"{THIS_MODULE}.OtherPlugin"])


class TestSettingsCheck(unittest.TestCase):
    def test_unregisteredPluginInSettingsFile(self):
        """Loading a settings file whose plugins are missing from the configured App gives a clear error."""
        with directoryChangers.TemporaryDirectoryChanger():
            with open("case.yaml", "w") as f:
                f.write(
                    textwrap.dedent(f"""\
                    settings:
                      plugins:
                        - {THIS_MODULE}.OtherPlugin
                    """)
                )
            with self.assertRaisesRegex(RuntimeError, "OtherPlugin"):
                caseSettings.Settings("case.yaml")

    def test_registeredPluginInSettingsFile(self):
        """Plugins that the App already has (e.g. from an App subclass) are fine."""
        with directoryChangers.TemporaryDirectoryChanger():
            with open("case.yaml", "w") as f:
                f.write("settings:\n  plugins:\n    - armi.physics.neutronics.NeutronicsPlugin\n")
            cs = caseSettings.Settings("case.yaml")
        self.assertEqual(cs["plugins"], ["armi.physics.neutronics.NeutronicsPlugin"])
