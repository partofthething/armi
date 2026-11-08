# Copyright 2019 TerraPower, LLC
# Copyright 2026 Nick Touran
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

"""
The base ARMI App class.

This module defines the :py:class:`App` class, which is used to configure the ARMI
Framework for a specific application. An ``App`` implements a simple interface for
customizing much of the Framework's behavior.
"""

# ruff: noqa: E402
import collections
import importlib
import importlib.util
import os
import sys
from typing import Dict, Iterable, List, Optional, Tuple, Type

from armi import context, meta, pluginManager, plugins, settings
from armi.reactor import parameters
from armi.reactor.flags import Flags
from armi.settings import Setting, fwSettings

PLUGINS_ENV_VAR = "ARMI_PLUGINS"
"""Environment variable holding a comma-separated list of extra plugins for the default ARMI App."""


class App:
    """
    The highest-level of abstraction for defining what happens during an ARMI run.

    .. impl:: An App has a plugin manager.
        :id: I_ARMI_APP_PLUGINS
        :implements: R_ARMI_APP_PLUGINS

        The App class is intended to be subclassed in order to customize the functionality
        and look-and-feel of the ARMI Framework for a specific use case. An App contains a
        plugin manager, which should be populated in ``__init__()`` with a collection of
        plugins that are deemed suitable for a given application, as well as other methods
        which provide further customization.

        The base App class is also a good place to expose some more convenient ways to get
        data out of the Plugin API; calling the ``pluggy`` hooks directly can sometimes be a
        pain, as the results returned by the individual plugins may need to be merged and/or
        checked for errors. Adding that logic here reduces boilerplate throughout the rest
        of the code.
    """

    name = "armi"
    """
    The program name of the app. This should be the actual name of the python entry
    point that loads the app, or the name of the module that contains the appropriate
    __main__ function. For example, if the app is expected to be invoked with ``python
    -m myapp``, ``name`` should be ``"myapp"``
    """

    def __init__(self):
        """
        Initialize the default plugin manager. Subclasses are free to adopt this plugin manager and register more
        plugins of their own, or to throw it away and start from scratch if they do not wish to use the default
        Framework plugins.

        For a description of the things that an ARMI plugin can do, see the :py:mod:`armi.plugins` module.
        """
        self._pluginFlagsRegistered: bool = False
        self._pm: Optional[pluginManager.ArmiPluginManager] = None
        self._paramRenames: Optional[Tuple[Dict[str, str], int]] = None
        self.__initNewPlugins()

    def __initNewPlugins(self):
        from armi import bookkeeping, cli, reactor
        from armi.physics import (
            economics,
            fuelCycle,
            fuelPerformance,
            neutronics,
            safety,
            thermalHydraulics,
        )

        self._pm = plugins.getNewPluginManager()
        for plugin in (
            cli.EntryPointsPlugin,
            bookkeeping.BookkeepingPlugin,
            economics.EconomicsPlugin,
            fuelCycle.FuelHandlerPlugin,
            fuelPerformance.FuelPerformancePlugin,
            neutronics.NeutronicsPlugin,
            safety.SafetyPlugin,
            thermalHydraulics.ThermalHydraulicsPlugin,
            reactor.ReactorPlugin,
        ):
            self._pm.register(plugin)

        self._paramRenames = None

    @property
    def version(self) -> str:
        """Grab the version of this app (defaults to ARMI version).

        Notes
        -----
        This is designed to be over-ridable by Application developers.
        """
        return meta.__version__

    @property
    def pluginManager(self) -> pluginManager.ArmiPluginManager:
        """Return the App's PluginManager."""
        return self._pm

    def getSettings(self) -> Dict[str, Setting]:
        """Return a dictionary containing all Settings defined by the framework and all plugins."""
        # Start with framework settings
        settingDefs = {setting.name: setting for setting in fwSettings.getFrameworkSettings()}

        # The optionsCache stores options that may have come from a plugin before the setting to
        # which they apply. Whenever a new setting is added, we check to see if there are any
        # options in the cache, popping them out and adding them to the setting. If all plugins'
        # settings have been processed and the cache is not empty, that's an error, because a plugin
        # must have provided options to a setting that doesn't exist.
        optionsCache: Dict[str, List[settings.Option]] = collections.defaultdict(list)
        defaultsCache: Dict[str, settings.Default] = {}

        for pluginSettings in self._pm.hook.defineSettings():
            for pluginSetting in pluginSettings:
                if isinstance(pluginSetting, settings.Setting):
                    name = pluginSetting.name
                    if name in settingDefs:
                        raise ValueError(f"The setting {pluginSetting.name} already exists and cannot be redefined.")
                    settingDefs[name] = pluginSetting
                    # handle when new setting has modifier in the cache (modifier loaded first)
                    if name in optionsCache:
                        settingDefs[name].addOptions(optionsCache.pop(name))
                    if name in defaultsCache:
                        settingDefs[name].changeDefault(defaultsCache.pop(name))
                elif isinstance(pluginSetting, settings.Option):
                    if pluginSetting.settingName in settingDefs:
                        # modifier loaded after setting, so just apply it (no cache needed)
                        settingDefs[pluginSetting.settingName].addOption(pluginSetting)
                    else:
                        # no setting yet, cache it and apply when it arrives
                        optionsCache[pluginSetting.settingName].append(pluginSetting)
                elif isinstance(pluginSetting, settings.Default):
                    if pluginSetting.settingName in settingDefs:
                        # modifier loaded after setting, so just apply it (no cache needed)
                        settingDefs[pluginSetting.settingName].changeDefault(pluginSetting)
                    else:
                        # no setting yet, cache it and apply when it arrives
                        defaultsCache[pluginSetting.settingName] = pluginSetting
                else:
                    raise TypeError(
                        "Invalid setting definition found: {} ({})".format(pluginSetting, type(pluginSetting))
                    )

        if optionsCache:
            raise ValueError(
                "The following options were provided for settings that do "
                "not exist. Make sure that the set of active plugins is "
                "consistent.\n{}".format(optionsCache)
            )

        if defaultsCache:
            raise ValueError(
                "The following defaults were provided for settings that do "
                "not exist. Make sure that the set of active plugins is "
                "consistent.\n{}".format(defaultsCache)
            )

        return settingDefs

    def getParamRenames(self) -> Dict[str, str]:
        """
        Return the parameter renames from all registered plugins.

        This renders a merged dictionary containing all parameter renames from all of the registered
        plugins. It also performs simple error checking. The result of this operation is cached,
        since it is somewhat expensive to perform. If the App detects that its plugin manager's set
        of registered plugins has changed, the cache will be invalidated and recomputed.
        """
        cacheInvalid = False
        if self._paramRenames is not None:
            renames, counter = self._paramRenames
            if counter != self._pm.counter:
                cacheInvalid = True
        else:
            cacheInvalid = True

        if cacheInvalid:
            currentNames = {pd.name for pd in parameters.ALL_DEFINITIONS}

            renames = dict()
            for pluginRenames in self._pm.hook.defineParameterRenames():
                collisions = currentNames & pluginRenames.keys()
                if collisions:
                    raise plugins.PluginError(
                        "The following parameter renames from a plugin collide with "
                        "currently-defined parameters:\n{}".format(collisions)
                    )
                pluginCollisions = renames.keys() & pluginRenames.keys()
                if pluginCollisions:
                    raise plugins.PluginError(
                        "The following parameter renames are already defined by another plugin:\n{}".format(
                            pluginCollisions
                        )
                    )
                renames.update(pluginRenames)
            self._paramRenames = renames, self._pm.counter
        return renames

    def registerPluginFlags(self):
        """
        Apply flags specified in the passed ``PluginManager`` to the ``Flags`` class.

        See Also
        --------
        armi.plugins.ArmiPlugin.defineFlags
        """
        if self._pluginFlagsRegistered:
            raise RuntimeError("Plugin flags have already been registered. Cannot do it twice!")

        for pluginFlags in self._pm.hook.defineFlags():
            Flags.extend(pluginFlags)

        self._pluginFlagsRegistered = True

    def registerPlugins(self, pluginPaths: Iterable[str]):
        """
        Register full-featured plugins, given as importable paths, before the app is configured.

        This lets settings choose additional plugins to add to any App, without
        writing a new App subclass. It is what powers the ``plugins`` setting and the ``ARMI_PLUGINS``
        environment variable (see :py:func:`armi.configure`). See :py:func:`importPlugin` for the
        accepted path formats. Plugins that are already registered are skipped.

        The plugins may define settings, parameters, and flags. Those are frozen when
        :py:func:`armi.configure` is called, so this must be called before the App is configured.
        """
        from armi import getApp

        if getApp() is self:
            raise RuntimeError(
                "Plugins cannot be registered after the App has been configured because their parameters and flags "
                "would be missed. Register them before calling `armi.configure()`."
            )
        for pluginPath in pluginPaths:
            if not self._isPluginRegistered(pluginPath):
                self._pm.register(importPlugin(pluginPath))

    def _isPluginRegistered(self, pluginPath: str):
        r"""
        Check if the plugin at the provided path is already registered.

        The expected path formats are:
        ------------------------------
        importable namespace:
        ``armi.stuff.plugindir.pluginMod.pluginCls`` or ``armi.stuff.plugindir.pluginMod:pluginCls``

        or on Linux/Unix:
        ``/path/to/pluginMod.py:pluginCls``

        or on Windows:
        ``C:\\path\\to\\pluginMod.py:pluginCls``

        Parameters
        ----------
        pluginPath : str
            String path to a plugin.

        Returns
        -------
        bool
            Whether or not the plugin name is already registered with the manager.
        """
        if ":" in pluginPath:
            pluginName = pluginPath.strip().split(":")[-1]
        else:
            pluginName = pluginPath.strip().split(".")[-1]

        return self._pm.has_plugin(pluginName)

    @property
    def splashText(self):
        """
        Return a textual splash screen.

        Specific applications will want to customize this, but by default the ARMI one
        is produced, with extra data on the App name and version, if available.
        """
        # typical ARMI splash text
        splash = r"""
+===================================================+
|            _      ____     __  __    ___          |
|           / \    |  _ \   |  \/  |  |_ _|         |
|          / _ \   | |_) |  | |\/| |   | |          |
|         / ___ \  |  _ <   | |  | |   | |          |
|        /_/   \_\ |_| \_\  |_|  |_|  |___|         |
|        Advanced  Reactor  Modeling Interface      |
|                                                   |
|                    version {0:10s}             |
|                                                   |""".format(meta.__version__)

        # add the name/version of the current App, if it's not the default
        if context.APP_NAME != "armi":
            from armi import getApp

            splash += r"""
|---------------------------------------------------|
|   {0:>17s} app version {1:10s}        |""".format(context.APP_NAME, getApp().version)

        # bottom border of the splash
        splash += r"""
+===================================================+
"""

        return splash


def importPlugin(pluginPath: str) -> Type[plugins.ArmiPlugin]:
    r"""
    Import and return the plugin class named by a string path.

    Accepted formats are:

    * an importable namespace: ``armi_openmc.plugin.OpenMCPlugin``
    * an entry-point style namespace: ``armi_openmc.plugin:OpenMCPlugin``
    * a Python file and class name: ``/path/to/myPlugin.py:MyPlugin`` (or ``C:\path\to\myPlugin.py:MyPlugin``)
    """
    pluginPath = pluginPath.strip()
    if ".py:" in pluginPath:
        filePath, className = pluginPath.rsplit(".py:", 1)
        spec = importlib.util.spec_from_file_location(className, filePath + ".py")
        mod = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = mod
        spec.loader.exec_module(mod)
    else:
        sep = ":" if ":" in pluginPath else "."
        modPath, _, className = pluginPath.rpartition(sep)
        mod = importlib.import_module(modPath)

    plugin = getattr(mod, className)
    if not (isinstance(plugin, type) and issubclass(plugin, plugins.ArmiPlugin)):
        raise TypeError(f"`{pluginPath}` does not name an ArmiPlugin subclass.")
    return plugin


def readPluginsFromSettingsFile(fName: str) -> List[str]:
    """
    Return the ``plugins`` listed in a settings file, without loading the rest of it.

    The full settings can only be read once the plugins that define them are registered, so this takes a quick look at
    just the ``plugins`` setting first. Files that are not ARMI settings files (e.g. blueprints) give an empty list.
    """
    from ruamel.yaml import YAML

    from armi.settings.fwSettings.globalSettings import CONF_PLUGINS

    with open(fName, "r") as f:
        tree = YAML(typ="safe").load(f)
    if not isinstance(tree, dict) or not isinstance(tree.get("settings"), dict):
        return []
    return [str(p) for p in tree["settings"].get(CONF_PLUGINS) or []]


def findRequestedPlugins(args: Iterable[str] = ()) -> List[str]:
    """
    Gather the extra plugins requested for an App, without duplicates.

    Plugins come from the ``ARMI_PLUGINS`` environment variable (comma-separated) and from the ``plugins`` setting of
    any existing YAML settings files among ``args`` (typically the command line arguments).
    """
    requested = [p.strip() for p in os.environ.get(PLUGINS_ENV_VAR, "").split(",") if p.strip()]
    for arg in args:
        if os.path.splitext(arg)[1].lower() in (".yaml", ".yml") and os.path.isfile(arg):
            try:
                requested.extend(readPluginsFromSettingsFile(arg))
            except Exception:  # noqa: BLE001 -- unreadable files are reported later by the real settings reader
                continue
    return list(dict.fromkeys(requested))
