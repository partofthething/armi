"""
The editing session, and the calls the browser makes against it.

One session holds at most one blueprints file and one settings file, which matches how ARMI cases
are put together and keeps the editor honest about what it is looking at. Handlers here take and
return plain data; turning that into HTTP is :py:mod:`armi.webui.server`'s job.

Errors are values, not exceptions: a handler returns ``(status, payload)`` so that a blueprint the
user has temporarily broken comes back as a message in the editor rather than a stack trace in a
terminal they may not be watching.
"""

import pathlib
import traceback

from armi import materials, runLog
from armi.settings.fwSettings.globalSettings import CONF_LOADING_FILE, CONF_MATERIAL_NAMESPACE_ORDER
from armi.webui import catalog, preview, runs, settingsdoc
from armi.webui.document import BlueprintsDocument, DocumentError

# Suffixes offered by the file picker.
BLUEPRINT_SUFFIXES = (".yaml", ".yml")


def _looksLikeSettings(path) -> bool:
    """Tell a case settings file from a blueprints file by its one distinguishing key."""
    try:
        with open(path) as stream:
            for line in stream:
                stripped = line.split("#", 1)[0].rstrip()
                if not stripped:
                    continue
                if stripped.startswith("settings:"):
                    return True
                if not stripped.startswith(" "):
                    return False
    except OSError:
        return False
    return False


class Session:
    """Everything one running editor knows about.

    Parameters
    ----------
    bpPath : str or pathlib.Path, optional
        Blueprints to open at startup.
    csPath : str or pathlib.Path, optional
        Settings file to open at startup.
    """

    def __init__(self, bpPath=None, csPath=None):
        self.doc = None
        self.settings = None
        self.dirty = False
        self.settingsDirty = False
        self.run = None
        self.rootDir = pathlib.Path.cwd()
        self._catalog = None
        self._catalogOrder = None

        if csPath:
            self._loadSettings(csPath)
        if bpPath:
            self._loadBlueprints(bpPath)

    @classmethod
    def forPath(cls, path):
        """Open whatever the command line pointed at."""
        session = cls()
        if path is not None:
            status, payload = session.open({"path": str(path)})
            if status >= 400:
                runLog.warning(payload.get("error", f"Could not open {path}."))
        return session

    def _loadSettings(self, path):
        self.settings = settingsdoc.SettingsDocument.fromFile(path)
        self.rootDir = self.settings.path.parent
        self._applyMaterialNamespaces()

    def _loadBlueprints(self, path):
        self.doc = BlueprintsDocument.fromFile(path)
        self.rootDir = self.doc.path.parent
        self.dirty = False

    def _applyMaterialNamespaces(self):
        """Let the open settings decide which materials the editor offers.

        Without this, a case built on an application's own material library would offer a menu of
        framework materials and none of the ones actually in use.
        """
        if self.settings is None:
            return
        order = self.settings.cs[CONF_MATERIAL_NAMESPACE_ORDER]
        if not order or order == self._catalogOrder:
            return
        try:
            materials.setMaterialNamespaceOrder(order)
        except Exception as ee:
            runLog.warning(f"Could not apply the material namespace order {order}: {ee}")
            return
        self._catalogOrder = order
        self._catalog = None

    # -- shared -----------------------------------------------------------------------------------

    @property
    def catalog(self) -> dict:
        """Materials, shapes and flags, worked out once per session."""
        if self._catalog is None:
            self._catalog = catalog.everything()
        return self._catalog

    def _shapesByKey(self) -> dict:
        byKey = {}
        for shape in self.catalog["shapes"]:
            byKey[shape["key"]] = shape
            byKey[shape["name"].lower()] = shape
        return byKey

    def state(self) -> dict:
        """What the editor shows in its title bar and file menu."""
        return {
            "app": settingsdoc.appName(),
            "blueprints": {
                "path": str(self.doc.path) if self.doc and self.doc.path else None,
                "open": self.doc is not None,
                "dirty": self.dirty,
                "includes": list(self.doc.includes) if self.doc else [],
            },
            "settings": {
                "path": str(self.settings.path) if self.settings and self.settings.path else None,
                "open": self.settings is not None,
                "dirty": self.settingsDirty,
            },
            "run": self.run.state if self.run else None,
            "cwd": str(self.rootDir),
        }

    # -- blueprints -------------------------------------------------------------------------------

    def getBlueprints(self):
        if self.doc is None:
            return 404, {"error": "No blueprints are open."}
        data = self.doc.toDict()
        return 200, {
            "document": data,
            "geometry": {name: self.doc.geometryOf(name) for name in data.get("grids", {})},
            "state": self.state(),
        }

    def putBlueprints(self, body):
        if self.doc is None:
            return 404, {"error": "No blueprints are open."}
        document = body.get("document")
        if not isinstance(document, dict):
            return 400, {"error": "Expected a 'document' object."}
        try:
            self.doc.apply(document)
        except DocumentError as ee:
            return 400, {"error": str(ee)}
        except Exception as ee:
            runLog.error(traceback.format_exc())
            return 400, {"error": f"{type(ee).__name__}: {ee}"}
        self.dirty = True
        data = self.doc.toDict()
        return 200, {
            "document": data,
            "geometry": {name: self.doc.geometryOf(name) for name in data.get("grids", {})},
            "state": self.state(),
        }

    def open(self, body):
        """Open whatever the user picked, and whatever comes with it.

        A case settings file names its blueprints in ``loadingFile``, so opening one opens both.
        Working that out here rather than in the browser means the answer comes from reading the
        file, not from guessing at its name.
        """
        path = body.get("path")
        if not path:
            return 400, {"error": "Say which file to open."}

        target = pathlib.Path(path).expanduser()
        if not target.is_file():
            return 400, {"error": f"There is no file at {path}."}

        try:
            if _looksLikeSettings(target):
                self._loadSettings(target)
                loading = self.settings.cs[CONF_LOADING_FILE]
                if not loading:
                    return 200, {"opened": "settings", **self.getSettings()[1], "state": self.state()}
                blueprints = (target.parent / loading).resolve()
                if not blueprints.is_file():
                    return 409, {
                        "error": f"{target.name} loads blueprints from {loading}, which is not there.",
                        "opened": "settings",
                        "state": self.state(),
                    }
                self._loadBlueprints(blueprints)
            else:
                self._loadBlueprints(target)
        except Exception as ee:
            return 400, {"error": f"Could not read {path}: {ee}"}

        status, payload = self.getBlueprints()
        payload["opened"] = "case" if self.settings else "blueprints"
        return status, payload

    def openBlueprints(self, body):
        """Open a blueprints file and nothing else."""
        path = body.get("path")
        if not path:
            return 400, {"error": "Say which file to open."}
        try:
            self._loadBlueprints(path)
        except Exception as ee:
            return 400, {"error": f"Could not read {path}: {ee}"}
        return self.getBlueprints()

    def newBlueprints(self, body):
        try:
            self.doc = BlueprintsDocument.blank(
                geom=body.get("geom", "hex"),
                symmetry=body.get("symmetry", "third periodic"),
            )
        except Exception as ee:
            return 400, {"error": str(ee)}
        self.dirty = True
        return self.getBlueprints()

    def saveBlueprints(self, body):
        if self.doc is None:
            return 404, {"error": "No blueprints are open."}
        try:
            written = self.doc.save(body.get("path"), flatten=bool(body.get("flatten")))
        except DocumentError as ee:
            return 409, {"error": str(ee), "needsFlatten": True}
        except Exception as ee:
            return 400, {"error": str(ee)}
        self.dirty = False
        self.rootDir = written.parent
        runLog.info(f"Wrote blueprints to {written}")
        return 200, {"path": str(written), "state": self.state()}

    def blueprintsYaml(self):
        if self.doc is None:
            return 404, {"error": "No blueprints are open."}
        return 200, {"yaml": self.doc.toYaml()}

    def expandGrid(self, body):
        if self.doc is None:
            return 404, {"error": "No blueprints are open."}
        name = body.get("name")
        if name not in self.doc.bp.gridDesigns:
            return 400, {"error": f"There is no grid called {name!r}."}
        try:
            self.doc.expandToFull(name)
        except Exception as ee:
            return 400, {"error": f"Could not expand {name} to full: {ee}"}
        self.dirty = True
        return self.getBlueprints()

    # -- previews ---------------------------------------------------------------------------------

    def previewBlock(self, body):
        """Lay out one block for drawing, without needing it to be valid enough to construct."""
        block = body.get("block")
        if not isinstance(block, dict):
            return 400, {"error": "Expected a 'block' object."}
        gridName = block.get("grid name")
        cells, geom = None, None
        if gridName and self.doc is not None and gridName in self.doc.bp.gridDesigns:
            # Straight at the grid, not through the whole document. Building the JSON view to read
            # one grid's positions cost about half a second, which is most of a click.
            cells = self.doc.cellsOf(gridName)
            geom = self.doc.geometryOf(gridName)
        try:
            return 200, preview.blockPreview(block, self._shapesByKey(), cells, geom)
        except Exception as ee:
            runLog.error(traceback.format_exc())
            return 400, {"error": f"{type(ee).__name__}: {ee}"}

    # -- settings ---------------------------------------------------------------------------------

    def getSettings(self):
        if self.settings is None:
            self.settings = settingsdoc.SettingsDocument.blank()
        return 200, self.settings.toDict()

    def putSettings(self, body):
        if self.settings is None:
            self.settings = settingsdoc.SettingsDocument.blank()
        values = body.get("values")
        if not isinstance(values, dict):
            return 400, {"error": "Expected a 'values' object."}
        try:
            self.settings.apply(values)
        except settingsdoc.SettingsError as ee:
            return 400, {"error": str(ee)}
        self.settingsDirty = True
        return 200, self.settings.toDict()

    def saveSettings(self, body):
        if self.settings is None:
            return 404, {"error": "No settings are open."}
        try:
            written = self.settings.save(body.get("path"), style=body.get("style", "short"))
        except settingsdoc.SettingsError as ee:
            return 409, {"error": str(ee), "ignored": list(self.settings.ignored)}
        except Exception as ee:
            return 400, {"error": str(ee)}
        self.settingsDirty = False
        return 200, {"path": str(written), "state": self.state()}

    # -- running the case -------------------------------------------------------------------------

    def startRun(self, body):
        """Run the open case as it is saved on disk."""
        if self.run is not None and self.run.running:
            return 409, {"error": "A run is already going. Stop it first."}
        if self.settings is None or self.settings.path is None:
            return 400, {"error": "Open or save a case settings file to run it."}
        if self.dirty or self.settingsDirty:
            return 409, {"error": "Save your changes first: a run uses the files as they are on disk."}
        try:
            self.run = runs.CaseRun(self.settings.path)
        except OSError as ee:
            return 500, {"error": f"Could not start the run: {ee}"}
        runLog.important(f"Started `{' '.join(self.run.command)}` in {self.run.workdir}")
        return 200, self.run.read(0)

    def runStatus(self, query):
        if self.run is None:
            return 200, {"state": None}
        offset = query.get("offset")
        try:
            offset = int(offset) if offset is not None else None
        except ValueError:
            return 400, {"error": "offset must be a whole number of bytes"}
        return 200, self.run.read(offset)

    def stopRun(self, body):
        if self.run is None or not self.run.running:
            return 409, {"error": "Nothing is running."}
        self.run.stop()
        return 200, self.run.read(None)

    # -- file picker ------------------------------------------------------------------------------

    def listFiles(self, query):
        """Directories and files under a directory, for the editor's file picker.

        A browser cannot see the filesystem, and a local tool should not make people paste absolute
        paths, so the editor gets its own small picker. ``kind`` is ``yaml`` (the default, for opening
        inputs), ``any`` (every file, for a path setting) or ``dir`` (folders only).
        """
        kind = query.get("kind") or "yaml"
        if kind not in ("yaml", "any", "dir"):
            return 400, {"error": f"Unknown file kind {kind!r}."}
        raw = query.get("dir") or str(self.rootDir)
        here = pathlib.Path(raw).expanduser()
        if not here.is_dir():
            here = here.parent if here.parent.is_dir() else pathlib.Path.home()
        here = here.resolve()

        directories, files = [], []
        try:
            for entry in sorted(here.iterdir(), key=lambda each: each.name.lower()):
                if entry.name.startswith("."):
                    continue
                if entry.is_dir():
                    directories.append({"name": entry.name, "path": str(entry)})
                elif kind == "any" or (kind == "yaml" and entry.suffix.lower() in BLUEPRINT_SUFFIXES):
                    files.append({"name": entry.name, "path": str(entry), "size": entry.stat().st_size})
        except PermissionError:
            return 403, {"error": f"Cannot read {here}."}

        return 200, {
            "dir": str(here),
            "parent": str(here.parent) if here.parent != here else None,
            "directories": directories,
            "files": files,
        }
