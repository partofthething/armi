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
"""
Blueprints as an editable JSON document.

The browser cannot hold a ``yamlize`` object graph, and we do not want a second, parallel
description of what a blueprint is. So the document here is just the blueprint YAML parsed into
plain dictionaries, with two deliberate departures:

Grids carry ``cells``
    A mapping of ``"i,j"`` to specifier, instead of an ascii ``lattice map`` or the ``grid
    contents`` complex-key form. Both input styles read into the same thing, and ARMI already knows
    how to write either one back out, so the editor only ever deals in cells.

Numbers keep their type
    JavaScript collapses ``20.0`` to ``20`` on the way through ``JSON.stringify``. Saving a file you
    only looked at should not rewrite every float in it, so floats that survive a round trip
    unchanged are restored before the YAML is written.

Assemblies list block names
    On disk an axial stack is a list of YAML aliases pointing back at anchored block definitions.
    That is identity, not text: two aliases to the same anchor are the same block design. JSON has
    no aliases, so copying the YAML structure verbatim would silently turn one shared design into
    eighteen unrelated copies. Instead an assembly holds the *names* of its blocks, which is what
    the stack editor wants anyway, and the anchors are rebuilt on the way out.

Every save goes back through ``Blueprints.load``, so a document that cannot be read by ARMI never
reaches the disk.
"""

import copy
import io
import pathlib
import re

from ruamel.yaml import YAML

from armi.reactor import blueprints, geometry
from armi.reactor.blueprints import gridBlueprint
from armi.utils import textProcessors

# Sections in the order they are written, which is also the order the editor lists them in.
SECTIONS = (
    "nuclide flags",
    "custom isotopics",
    "components",
    "component groups",
    "blocks",
    "assemblies",
    "systems",
    "grids",
)

# Keys the editor replaces with ``cells``; they are regenerated from cells on the way out.
_GRID_CONTENT_KEYS = ("lattice map", "grid contents")

_CELL_KEY = re.compile(r"^\s*(-?\d+)\s*,\s*(-?\d+)\s*$")

# Keys that sit alongside assembly names under ``assemblies:`` but are not assemblies.
_ASSEMBLY_LIST_KEYS = ("heights", "axial mesh points")


class DocumentError(ValueError):
    """The document cannot be turned into blueprints ARMI would accept."""


def cellKey(i, j) -> str:
    """Format a grid index pair as the string key used in ``cells``."""
    return f"{int(i)},{int(j)}"


def parseCellKey(key):
    """Read a ``"i,j"`` cell key back into a pair of ints."""
    match = _CELL_KEY.match(str(key))
    if match is None:
        raise DocumentError(f"Grid cell key {key!r} is not of the form 'i,j'.")
    return int(match.group(1)), int(match.group(2))


def _plainify(obj):
    """Strip ruamel's comment-carrying containers down to plain JSON-able types."""
    if isinstance(obj, dict):
        return {str(key): _plainify(value) for key, value in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_plainify(value) for value in obj]
    if isinstance(obj, bool) or obj is None:
        return obj
    if isinstance(obj, int):
        return int(obj)
    if isinstance(obj, float):
        return float(obj)
    return str(obj) if not isinstance(obj, str) else obj


def _restoreFloats(new, old):
    """Put back the floatness that a trip through JavaScript rounded off.

    Walks the incoming payload alongside the document the browser was given, and where a whole
    number now sits in place of a float, makes it a float again. Anything structurally new is left
    exactly as it arrived.
    """
    if isinstance(new, dict) and isinstance(old, dict):
        return {key: _restoreFloats(value, old.get(key)) for key, value in new.items()}
    if isinstance(new, list) and isinstance(old, list):
        return [_restoreFloats(value, old[i] if i < len(old) else None) for i, value in enumerate(new)]
    if isinstance(new, int) and not isinstance(new, bool) and isinstance(old, float):
        return float(new)
    return new


def _cellOnlyChanges(new: dict, old):
    """Work out whether an edit touched nothing but grid positions.

    Painting is the one edit that happens by the dozen, and rebuilding the whole blueprints to
    check a changed string in a dictionary is most of a second on a real core. Everything outside
    the grids has to be identical for this to apply, so whatever is skipped is something ARMI
    already accepted unchanged.

    Returns the new cells per grid, or None if the full path is needed.
    """
    if not isinstance(old, dict) or set(new) != set(old):
        return None

    changed = {}
    for section, value in new.items():
        if section != "grids":
            if value != old[section]:
                return None
            continue
        newGrids, oldGrids = value or {}, old[section] or {}
        if set(newGrids) != set(oldGrids):
            return None
        for name, grid in newGrids.items():
            mine, theirs = dict(grid), dict(oldGrids[name])
            cells, wereCells = mine.pop("cells", None), theirs.pop("cells", None)
            if mine != theirs:
                return None
            if cells != wereCells:
                changed[name] = cells
    return changed


def _namesToBlocks(data: dict):
    """Point each assembly's stack back at the block definitions themselves.

    Handing the very same dictionary object to several places is what makes ruamel write an anchor
    once and an alias everywhere after, which is both how ARMI blueprints are conventionally
    written and what yamlize needs in order to read the stack back.
    """
    blocks = data.get("blocks") or {}
    for assemName, assembly in (data.get("assemblies") or {}).items():
        if assemName in _ASSEMBLY_LIST_KEYS or not isinstance(assembly, dict):
            continue
        stack = assembly.get("blocks")
        if not isinstance(stack, list):
            continue
        assembly["blocks"] = [blocks[name] if isinstance(name, str) else name for name in stack]


def _yaml():
    """The round-trip flavour, used for anything that has to preserve anchors and aliases."""
    yaml = YAML(typ="rt")
    yaml.default_flow_style = False
    yaml.width = 120
    return yaml


def _fastYaml():
    """A plain loader for turning dumped YAML back into data we are about to flatten anyway.

    Round-trip mode exists to preserve comments and anchors through an edit. The JSON view keeps
    neither, and the round trip costs about a third of the time it takes to build that view on a
    reactor-sized file, so it is not worth paying for.
    """
    return YAML(typ="safe")


class BlueprintsDocument:
    """One blueprints file, open for editing.

    Parameters
    ----------
    bp : armi.reactor.blueprints.Blueprints
        The loaded blueprints.
    path : str or pathlib.Path, optional
        Where it came from, and where :py:meth:`save` writes by default.
    """

    def __init__(self, bp, path=None, includes=()):
        self.bp = bp
        self.path = pathlib.Path(path) if path else None
        self.includes = list(includes)
        self._lastServed = None
        # Building the JSON view means dumping the blueprints to YAML and parsing them back, which
        # is most of half a second on a real core. It is a pure function of `self.bp`, so it is
        # worked out once and thrown away whenever the blueprints change.
        self._dictCache = None
        self._geometryCache = {}

    def _invalidate(self):
        """Forget everything derived from the current blueprints."""
        self._dictCache = None
        self._geometryCache = {}

    @classmethod
    def fromFile(cls, path):
        """Read blueprints from a YAML file, following any ``!include`` it uses.

        Includes are a pre-processor step in ARMI, not part of the YAML, so they are pasted in
        before parsing. Which files were pulled in is remembered, because writing the document back
        out would flatten them all into one file and that should never happen by accident.
        """
        path = pathlib.Path(path).resolve()
        includes = [str(each) for each, _mark in textProcessors.findYamlInclusions(path)]
        stream = textProcessors.resolveMarkupInclusions(path)
        return cls(blueprints.Blueprints.load(stream), path, includes)

    @classmethod
    def fromText(cls, text, path=None):
        """Read blueprints from a string of YAML."""
        return cls(blueprints.Blueprints.load(io.StringIO(text)), path)

    @classmethod
    def blank(cls, geom=geometry.HEX, symmetry="third periodic"):
        """Start a new blueprints with one empty core grid and nothing else in it."""
        pitch = {"hex": 1.0} if geometry.GeomType.fromStr(geom) == geometry.GeomType.HEX else {"x": 1.0, "y": 1.0}
        skeleton = {
            "blocks": {},
            "assemblies": {},
            "systems": {"core": {"grid name": "core", "origin": {"x": 0.0, "y": 0.0, "z": 0.0}}},
            "grids": {"core": {"geom": str(geom), "symmetry": str(symmetry), "lattice pitch": pitch}},
        }
        stream = io.StringIO()
        _yaml().dump(skeleton, stream)
        return cls.fromText(stream.getvalue())

    # -- reading ---------------------------------------------------------------------------------

    def toDict(self) -> dict:
        """The whole document, as the browser sees it."""
        if self._dictCache is not None:
            return copy.deepcopy(self._dictCache)
        stream = io.StringIO()
        gridBlueprint.saveToStream(stream, self.bp, full=True, tryMap=True)
        stream.seek(0)
        data = _plainify(_fastYaml().load(stream)) or {}

        data["grids"] = self._gridsToDict(data.get("grids") or {})
        self._blocksToNames(data)
        for section in SECTIONS:
            data.setdefault(section, {} if section != "component groups" else None)

        self._lastServed = copy.deepcopy(data)
        self._dictCache = copy.deepcopy(data)
        return data

    def _gridsToDict(self, dumped: dict) -> dict:
        """Swap each grid's ascii map or contents dict for a flat cell mapping."""
        grids = {}
        for name, design in self.bp.gridDesigns.items():
            design._readGridContents()
            raw = dict(dumped.get(name) or {})
            for key in _GRID_CONTENT_KEYS:
                raw.pop(key, None)
            raw["cells"] = {cellKey(i, j): str(spec) for (i, j), spec in sorted(design.gridContents.items())}
            grids[name] = raw
        return grids

    def _blocksToNames(self, data: dict):
        """Replace each assembly's inlined block definitions with the names they refer to.

        Which definition an alias points at is a question about the object graph, not the text, so
        this asks the loaded blueprints rather than trying to match dictionaries. A block defined
        inline in an assembly rather than up in ``blocks:`` is hoisted up there and given the name
        it already carries, so the editor only ever has one place to look for a block design.
        """
        blocks = data.setdefault("blocks", {}) or {}
        data["blocks"] = blocks
        assemblies = data.get("assemblies") or {}
        nameById = {id(design): name for name, design in (self.bp.blockDesigns or {}).items()}

        for assemName, design in (self.bp.assemDesigns or {}).items():
            dumped = assemblies.get(assemName)
            if not isinstance(dumped, dict):
                continue
            inlined = dumped.get("blocks") or []
            names = []
            for index, block in enumerate(design.blocks):
                name = nameById.get(id(block)) or block.name
                if name not in blocks and index < len(inlined):
                    hoisted = dict(inlined[index])
                    hoisted.pop("name", None)
                    blocks[name] = hoisted
                names.append(name)
            dumped["blocks"] = names

    def cellsOf(self, name: str) -> dict:
        """One grid's positions, without building the whole document to get at them."""
        design = self.bp.gridDesigns[name]
        design._readGridContents()
        return {cellKey(i, j): str(spec) for (i, j), spec in design.gridContents.items()}

    def geometryOf(self, name: str) -> dict:
        if name in self._geometryCache:
            return self._geometryCache[name]
        info = self._computeGeometry(name)
        self._geometryCache[name] = info
        return info

    def _computeGeometry(self, name: str) -> dict:
        """How to lay a grid out on screen.

        Returns the two basis vectors that carry an ``(i, j)`` index to a centimeter position, which
        is all the browser needs to place a cell that does not exist yet. ARMI's own grid objects
        are the source of those vectors, so hex, corners-up hex and offset Cartesian all come out
        right without the browser knowing the difference.
        """
        design = self.bp.gridDesigns[name]
        geomType = geometry.GeomType.fromStr(design.geom)
        symmetry = geometry.SymmetryType.fromStr(design.symmetry)
        info = {
            "name": name,
            "geom": str(design.geom),
            "symmetry": str(design.symmetry),
            "domain": str(symmetry.domain),
            "boundary": str(symmetry.boundary),
            "throughCenter": bool(symmetry.isThroughCenterAssembly),
            "shape": "hex" if geomType == geometry.GeomType.HEX else "square",
            # GeomType folds corners-up into plain HEX, so the orientation only survives as a string.
            "cornersUp": str(design.geom).strip().lower() == geometry.HEX_CORNERS_UP,
        }
        try:
            grid = design.construct()
        except Exception as ee:
            info["error"] = str(ee)
            info["iStep"] = [1.0, 0.0]
            info["jStep"] = [0.0, 1.0]
            return info

        steps = grid._unitSteps
        info["iStep"] = [float(steps[0][0]), float(steps[1][0])]
        info["jStep"] = [float(steps[0][1]), float(steps[1][1])]
        info["offset"] = [float(grid._offset[0]), float(grid._offset[1])]

        # Without a lattice pitch ARMI builds a unit grid and takes real dimensions from the blocks
        # later, so the drawing is in grid units and should not claim to be in centimetres.
        pitch = design.latticeDimensions
        info["hasPitch"] = bool(pitch and any([pitch.hex, pitch.x, pitch.y]))
        # A lopsided full-core Cartesian grid has to be written as an explicit list of positions
        # rather than a readable map, and the editor is where that is worth knowing.
        info["centered"] = gridBlueprint.isCenteredOnOrigin(design)
        info["paintable"] = self._paintable(design, grid)
        return info

    @staticmethod
    def _paintable(design, grid) -> list:
        """Empty positions next to occupied ones that the grid's symmetry actually allows.

        An editor that offers a position outside a third-core wedge is offering to make an edit
        ARMI will silently discard on the way out, so the domain check is the same one the writer
        uses rather than a guess about which cells look reasonable.
        """
        contents = design.gridContents or {}
        geomType = geometry.GeomType.fromStr(design.geom)
        offsets = (
            [(1, 0), (-1, 0), (0, 1), (0, -1), (1, -1), (-1, 1)]
            if geomType == geometry.GeomType.HEX
            else [(1, 0), (-1, 0), (0, 1), (0, -1), (1, 1), (-1, -1), (1, -1), (-1, 1)]
        )

        candidates = set()
        for i, j in contents:
            for di, dj in offsets:
                if (i + di, j + dj) not in contents:
                    candidates.add((i + di, j + dj))
        if not contents:
            candidates = {(0, 0)}

        out = []
        for i, j in sorted(candidates):
            try:
                if grid.locatorInDomain(grid[i, j, 0], symmetryOverlap=False):
                    out.append(cellKey(i, j))
            except Exception:
                # A grid that cannot answer the question just does not offer that position.
                continue
        return out

    def expandToFull(self, name: str):
        """Unfold a symmetric grid into an explicit full-core one."""
        self.bp.gridDesigns[name].expandToFull()
        self._invalidate()

    # -- writing ---------------------------------------------------------------------------------

    def apply(self, payload: dict):
        """Replace the document with what the browser sent, if ARMI can read it.

        The blueprints are rebuilt from scratch rather than patched, so there is no way for the
        in-memory object graph to drift away from what the editor is showing.
        """
        if self._lastServed is not None:
            payload = _restoreFloats(payload, self._lastServed)

        cellsOnly = _cellOnlyChanges(payload, self._lastServed)
        if cellsOnly is not None:
            self._invalidate()
            settled = copy.deepcopy(payload)
            for name in cellsOnly:
                self._setCells(name, cellsOnly[name])
                # Re-read the positions rather than trusting the payload's ordering, so the
                # document the browser gets back is byte-for-byte what a full rebuild would give.
                settled["grids"][name]["cells"] = self.cellsOf(name)
            self._lastServed = copy.deepcopy(settled)
            self._dictCache = settled
            return

        data = copy.deepcopy(payload)
        cells = {}
        for name, grid in (data.get("grids") or {}).items():
            cells[name] = grid.pop("cells", None)
            for key in _GRID_CONTENT_KEYS:
                grid.pop(key, None)

        # Empty sections are noise in the file; drop them rather than writing `blocks: {}`.
        data = {key: value for key, value in data.items() if value not in ({}, [], None)}
        ordered = {key: data[key] for key in SECTIONS if key in data}
        ordered.update({key: value for key, value in data.items() if key not in SECTIONS})

        try:
            _namesToBlocks(ordered)
        except KeyError as ee:
            raise DocumentError(f"An assembly refers to block {ee}, which is not defined.") from ee

        stream = io.StringIO()
        _yaml().dump(ordered, stream)
        stream.seek(0)
        try:
            bp = blueprints.Blueprints.load(stream)
        except Exception as ee:
            raise DocumentError(str(ee)) from ee

        self.bp = bp
        for name, cellMap in cells.items():
            if cellMap is None:
                continue
            self._setCells(name, cellMap)
        self._invalidate()

    def _setCells(self, name: str, cells: dict):
        """Put a grid's positions in place, dropping any ascii map they came from.

        The map was only ever one way of writing these positions down; it is rebuilt from them on
        the way out, and keeping a stale one around would make it the source of truth again.
        """
        design = self.bp.gridDesigns[name]
        design.gridContents = {parseCellKey(key): str(spec) for key, spec in (cells or {}).items()}
        design.latticeMap = None
        design.readFromLatticeMap = True

    def toYaml(self) -> str:
        """The document as the YAML that would be written."""
        stream = io.StringIO()
        gridBlueprint.saveToStream(stream, self.bp, full=True, tryMap=True)
        return stream.getvalue()

    def save(self, path=None, flatten=False) -> pathlib.Path:
        r"""Write the blueprints to disk and remember where.

        Parameters
        ----------
        path : str, optional
            Where to write. Defaults to where the document was read from.
        flatten : bool, optional
            Required to overwrite a file that was assembled from ``!include``\ s, since writing it
            back collapses every included file into this one.
        """
        target = pathlib.Path(path).resolve() if path else self.path
        if target is None:
            raise DocumentError("This document has no path yet. Say where to save it.")
        if self.includes and target == self.path and not flatten:
            raise DocumentError(
                f"{target.name} is assembled from {len(self.includes)} included files "
                f"({', '.join(self.includes[:3])}"
                f"{', ...' if len(self.includes) > 3 else ''}). Writing it back would flatten them "
                "all into one file. Save to a new path, or say to flatten it."
            )
        text = self.toYaml()
        target.parent.mkdir(parents=True, exist_ok=True)
        with open(target, "w") as stream:
            stream.write(text)
        self.path = target
        return target
