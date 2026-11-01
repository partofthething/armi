"""
How a plugin says its settings should be edited.

An ARMI setting carries a type, a default, a description and sometimes a list of options, and for
most settings that is everything an editor needs. Two kinds of setting it is not enough for:

* a list whose entries follow a grammar the setting cannot express, which arrives as a text box
  and has to be typed from memory;
* a dict of dicts, one entry per object being configured, which arrives as a box of JSON.

The plugin that defined such a setting knows what belongs in it. This is where it says so. Give
the plugin class a ``defineSettingEditors(cs)`` static method returning ``{settingName: editor}``
and the editor renders a real control instead:

.. code-block:: python

    class MyPlugin(plugins.ArmiPlugin):
        @staticmethod
        def defineSettingEditors(cs):
            return {
                "coolingMode": {
                    "widget": "choice",
                    "options": ["forced", "natural"],
                    "group": "Cooling",
                },
            }

It is a plain method rather than a plugin hook because it describes the editor, not the
framework: a plugin that never mentions it runs exactly the same.

Three widgets, deliberately few. Each is a shape of value rather than a shape of problem, so
nothing here knows what any particular setting means:

``choice``
    One value out of a list. ``options`` are strings, or ``{"value", "label", "help"}`` mappings,
    and may be empty where there is nothing to offer yet; ``allowOther`` lets anything be typed,
    ``allowEmpty`` offers a blank.

``choices``
    Several values out of a list, for a list-valued setting. Same ``options``, plus ``allowOther``
    for entries that are not on it.

``table``
    A dict of dicts, one row per key. ``columns`` describe the fields of a row, each with its own
    ``widget`` of ``choice``, ``number`` or ``text``; ``rowLabel`` names what a row is and
    ``newRow`` is what an added one starts as.

Every editor may also carry:

``group``
    Moves the setting into a section of the editor's own.
``groupHint``
    A sentence or two shown at the top of that section, saying what its settings are for. Any one
    editor in the group may carry it; the first one found is used.
``order``
    A number that places the setting within its section, lowest first; settings without one follow,
    alphabetically. A plugin uses it to lead with the setting that matters most.
``hint``
    A line shown under the control.
``showWhen``
    ``{"setting": name, "equals": value}`` or ``{"setting": name, "in": [values]}``: show the
    setting only while another one has that value, so options that only apply to one choice of
    method stay out of the way until that method is chosen. A list of such conditions shows the
    setting while any one of them holds.

None of these needs a widget, which is how a plugin collects a handful of related settings into
one section while leaving the controls alone.

A fourth widget takes a file system path:

``path``
    A text box with a Browse button that opens the editor's own file picker. ``pathKind`` is
    ``"file"`` (the default) or ``"dir"``. An
editor naming a setting this application does not define is ignored, and one the editor cannot
make sense of is dropped with a warning rather than taking the settings page down.
"""

from armi import getPluginManagerOrFail, runLog

#: What a plugin calls its method.
HOOK = "defineSettingEditors"

#: Widgets the browser knows how to draw, and the key each one is built from. Options may be
#: an empty list -- a plugin offering the banks a core has is offering none until the core has
#: one, and that is a control with nothing in it rather than a mistake -- but a table with no
#: columns is nothing at all.
WIDGETS = {
    "choice": ("options", True),
    "choices": ("options", True),
    "table": ("columns", False),
    "path": (None, True),
}

#: What a path widget can ask the picker for.
PATH_KINDS = ("file", "dir")

#: Widgets a column of a table may use.
COLUMN_WIDGETS = ("choice", "number", "text")


class EditorError(ValueError):
    """An editor description that cannot be rendered."""


def collect(cs) -> dict:
    """Ask every registered plugin how the settings it owns should be edited.

    Parameters
    ----------
    cs : Settings
        The settings being edited, handed to the plugins so that an editor can be built from what
        is in the file: the banks a core actually has, the assemblies a design actually defines.
    """
    editors = {}
    for plugin in getPluginManagerOrFail().get_plugins():
        define = getattr(plugin, HOOK, None)
        if not callable(define):
            continue
        name = getattr(plugin, "__name__", str(plugin))
        try:
            described = define(cs) or {}
        except Exception as ee:
            runLog.warning(f"{name} could not describe its setting editors and was skipped: {ee}")
            continue
        for settingName, editor in described.items():
            if settingName not in cs:
                # Ordinary: a plugin may describe settings another plugin defines.
                continue
            try:
                editors[settingName] = normalize(editor)
            except EditorError as ee:
                runLog.warning(f"{name} describes {settingName} in a way this editor cannot use: {ee}")
    return editors


def normalize(editor: dict) -> dict:
    """Check one editor description and put it in the form the browser expects."""
    if not isinstance(editor, dict):
        raise EditorError("an editor must be a mapping")
    widget = editor.get("widget")
    if widget is not None and widget not in WIDGETS:
        raise EditorError(f"unknown widget {widget!r}; use one of {sorted(WIDGETS)}")
    if widget is not None:
        required, mayBeEmpty = WIDGETS[widget]
        if required and (editor.get(required) is None or (not editor[required] and not mayBeEmpty)):
            raise EditorError(f"a {widget} needs {required}")
    sayable = ("group", "groupHint", "hint", "showWhen", "order")
    if widget is None and not any(editor.get(key) is not None for key in sayable):
        raise EditorError("an editor with no widget has nothing to say but a group, hint, order or condition")

    out = {"widget": widget}
    for key in ("group", "groupHint", "hint", "rowLabel", "placeholder"):
        if editor.get(key):
            out[key] = str(editor[key])
    if editor.get("order") is not None:
        try:
            out["order"] = float(editor["order"])
        except (TypeError, ValueError):
            raise EditorError(f"order must be a number, not {editor['order']!r}") from None
    if editor.get("showWhen") is not None:
        conditions = editor["showWhen"]
        conditions = conditions if isinstance(conditions, (list, tuple)) else [conditions]
        if not conditions:
            raise EditorError("showWhen needs at least one condition")
        out["showWhen"] = [_condition(each) for each in conditions]
    if widget == "path":
        kind = editor.get("pathKind", "file")
        if kind not in PATH_KINDS:
            raise EditorError(f"pathKind must be one of {list(PATH_KINDS)}, not {kind!r}")
        out["pathKind"] = kind
    for key in ("allowOther", "allowEmpty"):
        if key in editor:
            out[key] = bool(editor[key])
    if widget in ("choice", "choices"):
        out["options"] = [_option(each) for each in editor["options"]]
    if widget == "table":
        out["columns"] = [_column(each) for each in editor["columns"]]
        out["newRow"] = dict(editor.get("newRow") or {})
    return out


def _condition(condition) -> dict:
    """When to show a setting: another setting equal to a value, or to one of several."""
    if not isinstance(condition, dict) or not condition.get("setting"):
        raise EditorError("showWhen needs the name of the setting it depends on")
    if ("equals" in condition) == ("in" in condition):
        raise EditorError("showWhen needs exactly one of 'equals' or 'in'")
    if "equals" in condition:
        return {"setting": str(condition["setting"]), "in": [condition["equals"]]}
    values = condition["in"]
    if isinstance(values, (str, bytes)) or not hasattr(values, "__iter__"):
        raise EditorError("showWhen 'in' needs a list of values")
    return {"setting": str(condition["setting"]), "in": list(values)}


def _option(option) -> dict:
    """One offered value, as ``{value, label, help}``."""
    if isinstance(option, dict):
        if "value" not in option:
            raise EditorError("an option needs a value")
        value = option["value"]
        return {
            "value": value,
            "label": str(option.get("label") or value),
            "help": str(option["help"]) if option.get("help") else None,
        }
    return {"value": option, "label": str(option), "help": None}


def _column(column) -> dict:
    """One field of a table row."""
    if not isinstance(column, dict) or "key" not in column:
        raise EditorError("a table column needs a key")
    widget = column.get("widget", "text")
    if widget not in COLUMN_WIDGETS:
        raise EditorError(f"a column cannot be a {widget!r}; use one of {list(COLUMN_WIDGETS)}")
    out = {
        "key": str(column["key"]),
        "label": str(column.get("label") or column["key"]),
        "widget": widget,
    }
    if column.get("options"):
        out["options"] = [_option(each) for each in column["options"]]
    for key in ("allowOther", "placeholder", "unit"):
        if column.get(key):
            out[key] = column[key] if key == "allowOther" else str(column[key])
    return out
