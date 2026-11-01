"""
A settings file, described well enough for a browser to build a form from it.

ARMI settings already carry everything a good editor needs: a type, a default, a description, the
legal options, and whether those options are the only ones allowed. None of that has to be written
down a second time here. What this module adds is grouping, so that a few hundred settings arrive
as something a person can navigate, and the round trip back to a YAML file.

Settings are grouped by whoever defined them, asked of each plugin directly. A plugin that adds
settings therefore gets its own section in the editor for free, and one that wants more than a
text box for a setting only it understands says so through :py:mod:`armi.webui.settingseditors`.
"""

import pathlib

from armi import getApp, getPluginManagerOrFail, runLog
from armi.settings import caseSettings, setting
from armi.settings.fwSettings import databaseSettings, globalSettings, reportSettings
from armi.webui import settingseditors

# Framework settings are one undifferentiated pile; split them the way their modules already do.
_FRAMEWORK_GROUPS = (
    ("General", globalSettings),
    ("Database", databaseSettings),
    ("Reports", reportSettings),
)


class SettingsError(ValueError):
    """The settings cannot be applied as given."""


def _groupNames() -> dict:
    """Setting name to the group heading it belongs under."""
    groups = {}
    for label, module in _FRAMEWORK_GROUPS:
        for each in module.defineSettings():
            if isinstance(each, setting.Setting):
                groups[each.name] = label

    pluginManager = getPluginManagerOrFail()
    for plugin in pluginManager.get_plugins():
        label = pluginManager.get_name(plugin) or getattr(plugin, "__name__", str(plugin))
        try:
            defined = plugin.defineSettings()
        except Exception:
            # A plugin with a broken hook should not take the settings editor down with it.
            continue
        for each in defined or ():
            if isinstance(each, setting.Setting):
                groups.setdefault(each.name, label)
    return groups


def _kindOf(each: setting.Setting) -> str:
    """What sort of control the browser should show."""
    underlying = each.underlyingType
    if underlying is bool:
        return "bool"
    if underlying is int:
        return "int"
    if underlying is float:
        return "float"
    if underlying in (list, tuple, set):
        return "list"
    if underlying is dict:
        return "dict"
    return "str"


def describe(cs: caseSettings.Settings) -> dict:
    """Every setting, its current value, and enough about it to render a control."""
    groups = _groupNames()
    editors = settingseditors.collect(cs)
    entries = []
    for name, each in sorted(cs.items(), key=lambda item: item[0].lower()):
        editor = editors.get(name)
        entries.append(
            {
                "name": name,
                "label": each.label,
                "description": each.description,
                # An editor may put its setting in a section of its own, which is how a plugin
                # with one setting worth finding and thirty that are not gets the one noticed.
                "group": (editor or {}).get("group") or groups.get(name, "Other"),
                "editor": editor,
                "kind": _kindOf(each),
                "value": each.dump(),
                "default": _jsonable(each.default),
                "options": list(each.options) if each.options else None,
                "enforced": bool(each.enforcedOptions),
                "subLabels": list(each.subLabels) if each.subLabels else None,
                "isDefault": each.isDefault(),
                "isEnvironment": bool(each.isEnvironment),
            }
        )
    # Within a section, settings an editor placed come first, in its order; the rest follow by name.
    entries.sort(key=lambda entry: (_order(entry), entry["name"].lower()))
    groupHints = {}
    for entry in entries:
        hint = (entry["editor"] or {}).get("groupHint")
        if hint:
            groupHints.setdefault(entry["group"], hint)
    return {
        "path": str(cs.path) if cs.path else None,
        "caseTitle": cs.caseTitle,
        "settings": entries,
        "groups": sorted({entry["group"] for entry in entries}, key=_groupSortKey),
        "groupHints": groupHints,
    }


def _order(entry) -> float:
    order = (entry["editor"] or {}).get("order")
    return order if order is not None else float("inf")


def _groupSortKey(name):
    order = {"General": 0, "Database": 1, "Reports": 2}
    return (order.get(name, 3), name.lower())


def _jsonable(value):
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(each) for each in value]
    if isinstance(value, dict):
        return {str(key): _jsonable(each) for key, each in value.items()}
    return str(value)


class SettingsDocument:
    """One settings file, open for editing."""

    def __init__(self, cs: caseSettings.Settings, path=None, ignored=()):
        self.cs = cs
        self.path = pathlib.Path(path) if path else None
        self.ignored = list(ignored)

    @classmethod
    def fromFile(cls, path):
        """Read a settings YAML file, without stopping to ask about the ones ARMI cannot place.

        Loading settings normally stops at the terminal to ask whether to continue past settings
        the running application does not define, which usually means a plugin is missing. The
        editor is a browser window, and the person who opened it is looking at that, not at the
        shell it was started from, so the question is answered here and asked again on screen.
        """
        path = pathlib.Path(path).resolve()
        cs = caseSettings.Settings()
        reader = cs.loadFromInputFile(str(path), handleInvalids=False)
        return cls(cs, path, ignored=sorted(reader.invalidSettings))

    @classmethod
    def blank(cls):
        """A settings object holding nothing but defaults."""
        return cls(caseSettings.Settings())

    def toDict(self) -> dict:
        described = describe(self.cs)
        described["path"] = str(self.path) if self.path else None
        described["ignored"] = list(self.ignored)
        return described

    def apply(self, values: dict):
        """Set the given settings, leaving everything else alone.

        Invalid values are reported all at once rather than one per attempt, because fixing five
        settings in five round trips is not how anyone wants to spend an afternoon.
        """
        problems = []
        for name, value in values.items():
            if name not in self.cs:
                problems.append(f"{name}: no such setting")
                continue
            try:
                self.cs = self.cs.modified(newSettings={name: value})
            except Exception as ee:
                problems.append(f"{name}: {ee}")
        if problems:
            raise SettingsError("; ".join(problems))

    def save(self, path=None, style="short") -> pathlib.Path:
        """Write the settings to disk.

        The default ``short`` style writes only what differs from the defaults, which is what keeps
        an ARMI settings file readable.
        """
        target = pathlib.Path(path).resolve() if path else self.path
        if target is None:
            raise SettingsError("This settings file has no path yet. Say where to save it.")
        if self.ignored and target == self.path:
            raise SettingsError(
                "This file has {} setting{} the running application does not define ({}). Saving "
                "over it would drop them. Start ARMI with the plugin that owns them, or save to a "
                "new path.".format(
                    len(self.ignored),
                    "" if len(self.ignored) == 1 else "s",
                    ", ".join(self.ignored[:4]) + (", ..." if len(self.ignored) > 4 else ""),
                )
            )
        target.parent.mkdir(parents=True, exist_ok=True)
        self.cs.writeToYamlFile(str(target), style=style)
        self.path = target
        runLog.info(f"Wrote settings to {target}")
        return target


def appName() -> str:
    """The name of the running ARMI application, shown in the editor's title bar."""
    app = getApp()
    return getattr(app, "name", "ARMI") if app else "ARMI"
