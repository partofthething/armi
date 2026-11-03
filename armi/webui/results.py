"""
Read an ARMI results database for the viewer, quickly.

:py:meth:`armi.bookkeeping.db.database.Database.load` rebuilds a whole reactor, which is the right
tool for analysis and far too slow for dragging a slider through two hundred time nodes. The
viewer only needs three things: where every assembly sat at each node, which rows of each
parameter dataset belong to which block, and the numbers themselves. This module reads those
straight out of the HDF5 file with numpy and never builds a composite.

How a node is described
-----------------------
The database stores the reactor as a flat, depth-first list of objects (the ``layout``). Two
things are worked out from it and cached separately, because they change at very different rates:

**Topology** (:py:class:`_Topology`)
    Which row is whose parent, which assemblies are in the core, which blocks are in each one and
    which components are in each block. It depends only on the object types and child counts, so
    it changes only when the model's structure does, such as when fuel is discharged to the pool.
    It takes a few small reads per node, so every node's topology is worked out on opening.
**Positions** (:py:meth:`ResultsDatabase.positionsOf`)
    Where each core assembly sits. A shuffle changes these but not the topology. They live in the
    same table as every pin's position, which can run to millions of rows, so they are picked out
    a node at a time, on demand and in the background. Nodes with the same arrangement share a
    *frame* id.

Values for a parameter are always sent in the topology's *canonical order*: assemblies in layout
order, blocks bottom to top within each, and components in layout order within each block. The
browser lays out the same order from the topology, so a parameter is just a flat array of numbers
with nothing else to say where each one goes.
"""

import copy
import hashlib
import pathlib
import re
import threading

import h5py
import numpy as np

from armi import runLog

# Datasets in a block group that are not parameters a person would want to colour a core by.
_SKIP_PARAMS = {"serialNum", "flags", "type", "xsType", "envGroup", "orientation", "pinIndices"}

# Component types whose shape the viewer knows how to draw, and the dimensions that say how.
_SHAPE_DIMS = {
    "Circle": ("od", "id"),
    "Hexagon": ("op", "ip"),
    "Square": ("widthOuter", "widthInner"),
    "Rectangle": ("lengthOuter", "lengthInner", "widthOuter", "widthInner"),
    "Helix": ("od", "id", "helixDiameter"),
    "DerivedShape": (),
}

_NUCLIDE_PARAM = re.compile(r"^n[A-Z][a-z]?\d*m?$|^nLfp\d+$|^nDump\d+$")


class ResultsError(Exception):
    """Something about the database the viewer cannot use."""


def _decode(values):
    return [v.decode() if isinstance(v, bytes) else str(v) for v in values]


def _resolveAttrs(dataset):
    """Attributes, following the ``@/path`` links ARMI uses for attributes too large to inline."""
    resolved = {}
    for key, value in dataset.attrs.items():
        if isinstance(value, str) and value.startswith("@"):
            resolved[key] = dataset.file[value[1:]][()]
        else:
            resolved[key] = value
    return resolved


def jaggedOwners(dataset):
    """For each value in a jagged ARMI dataset, which object it belongs to.

    ARMI flattens a ragged parameter into one array and records where each object's values start.
    Objects with nothing to store (``noneLocations``) get no start at all, so the k-th start belongs
    to the k-th object that has values, not to object k.
    """
    attrs = _resolveAttrs(dataset)
    offsets = np.asarray(attrs.get("offsets", []), dtype=np.int64)
    nones = np.asarray(attrs.get("noneLocations", []), dtype=np.int64).ravel()
    objects = np.delete(np.arange(len(offsets) + len(nones)), nones)
    if not len(offsets):
        return np.zeros(0, dtype=np.int64)
    return objects[np.searchsorted(offsets, np.arange(len(dataset)), side="right") - 1]


def perObject(dataset):
    """Each object's values of an array parameter, or None for an object without any.

    Reads a jagged dataset whose objects each hold a flat array, or a 2-D one, one row per object;
    returns None for any other kind.
    """
    attrs = _resolveAttrs(dataset)
    data = dataset[()]
    if attrs.get("jagged", False):
        offsets = np.asarray(attrs.get("offsets", []), dtype=np.int64)
        shapes = np.asarray(attrs.get("shapes", []), dtype=np.int64)
        if shapes.ndim != 2 or shapes.shape[1] != 1:
            return None
        nones = np.asarray(attrs.get("noneLocations", []), dtype=np.int64).ravel()
        owners = np.delete(np.arange(len(offsets) + len(nones)), nones)
        values = [None] * (len(offsets) + len(nones))
        for owner, start, size in zip(owners, offsets, shapes[:, 0]):
            values[owner] = np.asarray(data[start : start + size], dtype=np.float64)
        return values
    if data.ndim != 2 or dataset.dtype.kind not in "fiu":
        return None
    values = [np.asarray(row, dtype=np.float64) for row in data]
    if attrs.get("nones", False):
        values = [None if np.all(np.abs(row) > 1e300) else row for row in values]
    return values


#: Component parameters whose arrays are not one value per instance, even when the lengths happen to match.
_NOT_PER_INSTANCE = {"numberDensities", "nuclides", "detailedNDens", "pinNDens", "pinIndices"}


def isPerInstance(name, group) -> bool:
    """
    Whether a component dataset holds one value per instance, like ``pdens``.

    A component stands for ``mult`` alike instances (the pins of a pin array, the kernels of a
    particle fuel); a per-instance parameter gives each component exactly that many values. That is
    the test, rather than the name, so it holds whatever the instances are.
    """
    dataset = group[name]
    if name in _NOT_PER_INSTANCE or dataset.dtype.kind not in "fiu":
        return False
    values = perObject(dataset)
    if values is None:
        return False
    mult = group["mult"][()] if "mult" in group else np.ones(len(values))
    sized = [(v, m) for v, m in zip(values, mult) if v is not None]
    return bool(sized) and all(len(v) == int(round(float(m))) for v, m in sized)


def decodeFlags(data, flagOrder):
    """Turn ARMI's packed flag bytes into one string per object, like ``FUEL INNER``."""
    data = np.asarray(data, dtype=np.uint8)
    if data.ndim != 2:
        return [""] * len(data)
    bits = np.unpackbits(data, axis=1, bitorder="little")[:, : len(flagOrder)]
    names = np.array(_decode(flagOrder))
    return [" ".join(names[row.astype(bool)]) for row in bits]


class _Topology:
    """Who is whose parent, worked out once per distinct model structure."""

    def __init__(self, types, numChildren, locationTypes, names, gridIndex):
        rows = len(types)
        typeNames = np.array(_decode(types))
        self.types = typeNames

        parent = np.full(rows, -1, dtype=np.int64)
        stack = []  # [row, children still to come]
        children = numChildren.tolist()
        for row in range(rows):
            while stack and stack[-1][1] == 0:
                stack.pop()
            if stack:
                parent[row] = stack[-1][0]
                stack[-1][1] -= 1
            stack.append([row, children[row]])
        self.parent = parent

        # Each object has one location row, except multi-index locations, which have one per index.
        locTypes = _decode(locationTypes)
        extra = np.array([int(t[2:]) - 1 if t.startswith("M:") else 0 for t in locTypes], dtype=np.int64)
        self.locCount = extra + 1
        self.locStart = np.arange(rows) + np.concatenate([[0], np.cumsum(extra)[:-1]]) if rows else extra
        self.locIsNone = np.array([t == "N" for t in locTypes])

        # Where each object's parameters live in its type's datasets: the running count of its type.
        self.indexInData = np.zeros(rows, dtype=np.int64)
        for name in np.unique(typeNames):
            mask = typeNames == name
            self.indexInData[mask] = np.arange(mask.sum())

        names = np.array(_decode(names))
        coreRows = np.flatnonzero((typeNames == "Core") | (np.char.lower(names) == "core"))
        coreRows = coreRows[parent[coreRows] >= 0] if len(coreRows) > 1 else coreRows
        if not len(coreRows):
            raise ResultsError("This database has no core in it.")
        self.coreRow = int(coreRows[0])
        self.coreGrid = int(gridIndex[self.coreRow])

        self.assemblyRows = np.flatnonzero(parent == self.coreRow)
        isAssembly = np.zeros(rows, dtype=bool)
        isAssembly[self.assemblyRows] = True
        blockRows = np.flatnonzero(isAssembly[np.clip(parent, 0, None)] & (parent >= 0))
        # Depth-first order already has each assembly's blocks together and bottom to top.
        self.blockRows = blockRows
        self.blockAssembly = np.searchsorted(self.assemblyRows, parent[blockRows])
        self.blocksPerAssembly = np.bincount(self.blockAssembly, minlength=len(self.assemblyRows))
        self.blockStart = np.concatenate([[0], np.cumsum(self.blocksPerAssembly)])

        isBlock = np.zeros(rows, dtype=bool)
        isBlock[blockRows] = True
        compRows = np.flatnonzero(isBlock[np.clip(parent, 0, None)] & (parent >= 0))
        self.componentRows = compRows
        self.componentBlock = np.searchsorted(blockRows, parent[compRows])
        self.componentsPerBlock = np.bincount(self.componentBlock, minlength=len(blockRows))
        self.componentStart = np.concatenate([[0], np.cumsum(self.componentsPerBlock)])

        self._setNames(names)
        self.blockGrid = gridIndex[blockRows]
        self.blockTypes = typeNames[blockRows]
        self.assemblyTypes = typeNames[self.assemblyRows]
        self.componentTypes = typeNames[compRows]
        self.id = None
        self.firstStep = None
        self.structureKey = None
        self.designs = None

    def _setNames(self, names):
        self.assemblyNames = names[self.assemblyRows].tolist()
        self.blockNames = names[self.blockRows]
        self.componentNames = names[self.componentRows]

    def renamed(self, names):
        """The same structure with other names, as when fuel is discharged and fresh fuel takes its place.

        Everything but the names is shared with this topology, so this costs a decode of the names
        and nothing else.
        """
        twin = copy.copy(self)
        twin._setNames(np.array(_decode(names)))
        twin.id = None
        twin.firstStep = None
        twin.designs = None
        return twin

    def toJson(self):
        # Block names are left out: a long history with fuel discharged every cycle has a set of them
        # per cycle, which is megabytes nobody needs until they hover over a block. See ``names``.
        return {
            "id": self.id,
            "assemblyNames": self.assemblyNames,
            "blocksPerAssembly": self.blocksPerAssembly.tolist(),
            "blockCount": int(len(self.blockRows)),
            "componentCount": int(len(self.componentRows)),
        }


class ResultsDatabase:
    """One ARMI ``.h5`` file, open for reading.

    Opening is cheap: it lists the time nodes and works out each one's topology, which takes a few
    small reads per node. Where the assemblies sat is read per node on first use, since that means
    picking a few hundred rows out of a location table that also holds every pin; :py:meth:`warm`
    does all of that in the background so the viewer rarely has to wait for it.

    Parameters
    ----------
    path : str or pathlib.Path
        The database.
    """

    def __init__(self, path):
        self.path = pathlib.Path(path).resolve()
        self._stamp = self._stat()
        try:
            self.h5 = h5py.File(self.path, "r")
        except OSError as ee:
            raise ResultsError(f"{self.path.name} is not an HDF5 file h5py can read: {ee}") from ee
        self._lock = threading.RLock()
        self.caseTitle = str(self.h5.attrs.get("caseTitle", self.path.stem))
        self.steps = self._findSteps()
        if not self.steps:
            raise ResultsError(f"{self.path.name} has no time nodes in it.")
        self._structures = {}  # hash of the tree's shape -> the first topology with it
        self._topologies = {}  # hash of the shape and the names -> topology
        self._designCache = {}  # hash of the shape and component names -> block insides
        self._stepTopology = {}  # node -> topology
        self._positions = {}  # node -> (frame id, assembly i/j)
        self._frameIds = {}  # hash of positions -> frame id
        self._series = {}
        self._warmer = None
        self._closed = False
        self._coreGrid = False  # not looked for yet

    def _stat(self):
        try:
            stat = self.path.stat()
        except OSError:
            return None
        return (stat.st_ino, stat.st_mtime_ns, stat.st_size)

    def changedOnDisk(self) -> bool:
        """Whether the file has been replaced or rewritten since it was opened, as a running case's database is."""
        now = self._stat()
        return now is not None and now != self._stamp

    def close(self):
        with self._lock:
            self._closed = True
            self.h5.close()

    # -- time nodes -------------------------------------------------------------------------------

    def _findSteps(self):
        steps = []
        pattern = re.compile(r"^c(\d+)n(\d+)(.*)$")
        for key in self.h5.keys():
            match = pattern.match(key)
            if not match or not isinstance(self.h5[key], h5py.Group) or "layout" not in self.h5[key]:
                continue
            group = self.h5[key]
            cycle, node = int(match.group(1)), int(match.group(2))
            time = None
            reactor = next((group[name] for name in group if name.endswith("Reactor")), None)
            if reactor is not None and "time" in reactor:
                time = float(np.ravel(reactor["time"][()])[0])
            steps.append({"key": key, "cycle": cycle, "node": node, "label": match.group(3) or "", "time": time})
        steps.sort(key=lambda step: (step["cycle"], step["node"], step["label"]))
        return steps

    def stepKeys(self):
        return [step["key"] for step in self.steps]

    def _group(self, key):
        if key not in self.h5:
            raise ResultsError(f"There is no time node {key!r} in {self.path.name}.")
        return self.h5[key]

    # -- structure and positions ------------------------------------------------------------------

    def topologyOf(self, key) -> _Topology:
        """The model's structure at a node, shared with every other node built the same way."""
        with self._lock:
            if key in self._stepTopology:
                return self._stepTopology[key]
            layout = self._group(key)["layout"]
            types = layout["type"][()]
            numChildren = layout["numChildren"][()]
            locationTypes = layout["locationType"][()]
            names = layout["name"][()]
            structureKey = hashlib.blake2b(
                types.tobytes() + numChildren.tobytes() + locationTypes.tobytes(), digest_size=12
            ).hexdigest()
            # Names are keyed separately: discharging fuel and loading fresh assemblies in its place
            # changes the names and nothing else, and following an assembly needs the right ones.
            topoKey = structureKey + hashlib.blake2b(names.tobytes(), digest_size=8).hexdigest()
            topology = self._topologies.get(topoKey)
            if topology is None:
                structure = self._structures.get(structureKey)
                if structure is None:
                    topology = _Topology(types, numChildren, locationTypes, names, layout["gridIndex"][()])
                    self._structures[structureKey] = topology
                else:
                    topology = structure.renamed(names)
                topology.id = f"t{len(self._topologies)}"
                topology.firstStep = key
                topology.structureKey = structureKey
                self._topologies[topoKey] = topology
            self._stepTopology[key] = topology
            return topology

    def names(self, topologyId):
        """Every assembly and block name of a topology."""
        topo = self._topologyById(topologyId)
        return {"assemblyNames": topo.assemblyNames, "blockNames": topo.blockNames.tolist()}

    def _topologyById(self, topologyId):
        topo = next((each for each in self._topologies.values() if each.id == topologyId), None)
        if topo is None:
            raise ResultsError(f"There is no topology {topologyId!r}.")
        return topo

    def topologies(self):
        """Every node's topology, as ``(topologies by id, topology id per node)``."""
        perStep = [self.topologyOf(key) for key in self.stepKeys()]
        return {topo.id: topo for topo in perStep}, [topo.id for topo in perStep]

    def positionsOf(self, key):
        """Where each core assembly sat at a node, as ``(frame id, n-by-2 array of i, j)``.

        Nodes with the same structure and the same arrangement share a frame id, so the browser can
        tell a shuffle from a node where nothing moved without comparing anything itself.
        """
        with self._lock:
            if key in self._positions:
                return self._positions[key]
            topo = self.topologyOf(key)
            dataset = self._group(key)["layout/location"]
            rows = topo.locStart[topo.assemblyRows]
            # A point selection, not the whole table: the table holds every pin of every block and
            # can run to millions of rows, of which only a few hundred are wanted here.
            picked = dataset[np.sort(rows), :2] if len(rows) else np.zeros((0, 2))
            order = np.argsort(np.argsort(rows))
            ij = np.rint(picked[order]).astype(np.int64)
            frameKey = topo.id + hashlib.blake2b(ij.tobytes(), digest_size=8).hexdigest()
            frameId = self._frameIds.setdefault(frameKey, f"f{len(self._frameIds)}")
            self._positions[key] = (frameId, ij)
            return self._positions[key]

    def warm(self):
        """Read every node's positions on a background thread, a node at a time."""
        if self._warmer is not None:
            return self._warmer

        def work():
            for key in self.stepKeys():
                if self._closed:
                    return
                try:
                    self.positionsOf(key)
                except Exception as ee:  # pragma: no cover - a bad node should not stop the rest
                    runLog.debug(f"Could not read positions at {key}: {ee}")

        self._warmer = threading.Thread(target=work, name="armi-results-warm", daemon=True)
        self._warmer.start()
        return self._warmer

    def summary(self):
        """What the viewer needs before it can draw anything."""
        topologies, perStep = self.topologies()
        steps = [dict(step, topology=topoId) for step, topoId in zip(self.steps, perStep)]
        return {
            "path": str(self.path),
            "caseTitle": self.caseTitle,
            "steps": steps,
            "topologies": {topoId: topo.toJson() for topoId, topo in topologies.items()},
            "geometry": self.coreGeometry(),
            "parameters": self.parameters(),
            "core": self.coreHistory(),
        }

    def coreHistory(self):
        """Every scalar core and reactor parameter at every node, such as keff, as one list per parameter.

        These are a single number per node, so the whole history is small enough to send with the
        summary. A parameter missing at a node is None there.
        """
        with self._lock:
            found = {}
            for t, step in enumerate(self.steps):
                group = self._group(step["key"])
                for level in ("core", "reactor"):
                    typeName = next((name for name in group if name == level.capitalize()), None)
                    if typeName is None:
                        continue
                    for name, dataset in group[typeName].items():
                        if name in _SKIP_PARAMS or not isinstance(dataset, h5py.Dataset):
                            continue
                        if dataset.dtype.kind not in "fiu" or dataset.shape != (1,) or dataset.attrs.get("nones"):
                            continue
                        if name not in found:
                            found[name] = {"name": name, "level": level, "values": [None] * len(self.steps)}
                        value = float(dataset[0])
                        found[name]["values"][t] = value if np.isfinite(value) and abs(value) < 1e300 else None
            for level in ("core", "reactor"):
                _annotate(level, {name: entry for name, entry in found.items() if entry["level"] == level})
            return sorted(found.values(), key=lambda entry: entry["name"].lower())

    # -- grids ------------------------------------------------------------------------------------

    def _gridParams(self, key):
        gridGroup = self._group(key)["layout/grids"]
        found = []
        for index, gridType in enumerate(_decode(gridGroup["type"][()])):
            group = gridGroup[str(index)]
            found.append(
                {
                    "type": gridType,
                    "unitSteps": np.atleast_2d(group["unitSteps"][()]),
                    "geomType": group["geomType"].asstr()[()] if "geomType" in group else "",
                    "symmetry": group["symmetry"].asstr()[()] if "symmetry" in group else "",
                    "group": group,
                }
            )
        return found

    def coreGeometry(self):
        """How core grid indices become positions in centimetres, and what symmetry the core has."""
        key = self.steps[0]["key"]
        grid = self._gridParams(key)[self.topologyOf(key).coreGrid]
        return _planarGeometry(grid)

    def coreGrid(self):
        """ARMI's own grid object for the core, rebuilt from the stored parameters, or None if it cannot be."""
        if self._coreGrid is False:
            self._coreGrid = self._buildCoreGrid()
        return self._coreGrid

    def powerMultiplier(self) -> float:
        """How many copies of the modelled core make the whole core: 3 for a third, 1 for a full core."""
        grid = self.coreGrid()
        try:
            return float(grid.symmetry.symmetryFactor())
        except Exception:
            return 1.0

    def symmetricEquivalents(self, ij):
        """For a partial core, where each assembly's copies would sit in the full core, or None."""
        symmetry = (self.coreGeometry().get("symmetry") or "").lower()
        grid = None if not symmetry or symmetry.startswith("full") else self.coreGrid()
        if grid is None:
            return None
        out = []
        for i, j in ij.tolist():
            try:
                out.append([list(map(int, each[:2])) for each in grid.getSymmetricEquivalents((i, j, 0))])
            except Exception:
                return None
        return out

    def _buildCoreGrid(self):
        from armi.reactor import grids

        key = self.steps[0]["key"]
        coreGrid = self.topologyOf(key).coreGrid
        gridGroup = self._group(key)["layout/grids"][str(coreGrid)]
        gridType = _decode(self._group(key)["layout/grids/type"][()])[coreGrid]
        classes = {cls.__name__: cls for cls in _allSubclasses(grids.Grid)}
        cls = classes.get(gridType)
        if cls is None:
            return None
        bounds = [gridGroup[f"bounds_{i}"][()] if f"bounds_{i}" in gridGroup else None for i in range(3)]
        offset = gridGroup["offset"][()] if gridGroup.attrs.get("offset") else None
        params = grids.GridParameters(
            gridGroup["unitSteps"][()],
            bounds,
            gridGroup["unitStepLimits"][()],
            offset,
            gridGroup["geomType"].asstr()[()] if "geomType" in gridGroup else None,
            gridGroup["symmetry"].asstr()[()] if "symmetry" in gridGroup else None,
        )
        try:
            return cls(*params)
        except Exception as ee:
            runLog.debug(f"Could not rebuild the core grid for symmetry: {ee}")
            return None

    # -- what can be shown ------------------------------------------------------------------------

    def parameters(self):
        """Every numeric parameter at each level, with units and descriptions where ARMI knows them."""
        with self._lock:
            sampleKeys = sorted({self.steps[0]["key"], self.steps[len(self.steps) // 2]["key"], self.steps[-1]["key"]})
            topo = self.topologyOf(self.steps[0]["key"])
            levels = {
                "block": sorted(set(topo.blockTypes.tolist())),
                "assembly": sorted(set(topo.assemblyTypes.tolist())),
                "component": sorted(set(topo.componentTypes.tolist())),
            }
            out = {}
            nuclides = set()
            for level, typeNames in levels.items():
                found = {}
                for key in sampleKeys:
                    group = self._group(key)
                    for typeName in typeNames:
                        if typeName not in group:
                            continue
                        for name, dataset in group[typeName].items():
                            if name in _SKIP_PARAMS or name in found or not isinstance(dataset, h5py.Dataset):
                                continue
                            if level == "component" and name == "numberDensities" and "nuclides" in group[typeName]:
                                nuclides.update(_decode(np.unique(group[typeName]["nuclides"][()])))
                                continue
                            if level == "component" and isPerInstance(name, group[typeName]):
                                found[name] = {"name": name, "shape": [], "perInstance": True}
                                continue
                            entry = _describe(name, dataset)
                            if entry:
                                found[name] = entry
                _annotate(level, found)
                out[level] = sorted(found.values(), key=lambda entry: entry["name"].lower())
            out["componentNuclides"] = sorted(nuclide for nuclide in nuclides if nuclide)
            return out

    # -- values -----------------------------------------------------------------------------------

    def _gather(self, group, typeNames, indexInData, param, index=None, reduce=None):
        """Pull one parameter for a set of objects out of their per-type datasets."""
        values = np.full(len(typeNames), np.nan, dtype=np.float64)
        for typeName in np.unique(typeNames):
            if typeName not in group or param not in group[typeName]:
                continue
            dataset = group[typeName][param]
            if dataset.dtype.kind not in "fiub":
                continue
            attrs = dataset.attrs
            perInstance = isPerInstance(param, group[typeName])
            if perInstance:
                # One number per object: the mean of its instances.
                data = np.array([np.nan if v is None or not len(v) else float(np.mean(v)) for v in perObject(dataset)])
                index = reduce = None
            else:
                data = dataset[()]
            if attrs.get("jagged", False) and not perInstance:
                continue
            if data.ndim > 1:
                data = data.reshape(data.shape[0], -1)
                if index is not None:
                    data = data[:, int(index)] if int(index) < data.shape[1] else np.full(data.shape[0], np.nan)
                else:
                    data = {"sum": np.sum, "mean": np.mean, "min": np.min}.get(reduce, np.max)(data, axis=1)
            data = data.astype(np.float64)
            if attrs.get("nones", False):
                data[np.abs(data) > 1e300] = np.nan
            mask = typeNames == typeName
            rows = indexInData[mask]
            ok = rows < len(data)
            chunk = np.full(mask.sum(), np.nan)
            chunk[ok] = data[rows[ok]]
            values[mask] = chunk
        return values

    def series(self, level, param, index=None, reduce=None) -> bytes:
        """One parameter at every node, as little-endian float32 in each node's canonical order.

        The nodes are back to back. How many values each one has follows from its frame, which the
        browser already has, so nothing else needs to travel with the numbers.
        """
        if level not in ("block", "assembly"):
            raise ResultsError(f"There is no {level!r} level to show a whole history at.")
        cacheKey = (level, param, index, reduce)
        with self._lock:
            if cacheKey in self._series:
                return self._series[cacheKey]
            chunks = []
            for key in self.stepKeys():
                topo = self.topologyOf(key)
                group = self._group(key)
                if level == "block":
                    rows = topo.blockRows
                    typeNames = topo.blockTypes
                else:
                    rows = topo.assemblyRows
                    typeNames = topo.assemblyTypes
                chunks.append(self._gather(group, typeNames, topo.indexInData[rows], param, index, reduce))
            body = np.concatenate(chunks).astype("<f4").tobytes() if chunks else b""
            if len(self._series) > 24:
                self._series.pop(next(iter(self._series)))
            self._series[cacheKey] = body
            return body

    def componentValues(self, key, param, nuclide=None) -> bytes:
        """One component parameter at one node, as float32 in the frame's component order."""
        with self._lock:
            topo = self.topologyOf(key)
            group = self._group(key)
            rows = topo.componentRows
            typeNames = topo.componentTypes
            indexInData = topo.indexInData[rows]
            if nuclide:
                values = self._componentDensities(group, typeNames, indexInData, nuclide)
            else:
                values = self._gather(group, typeNames, indexInData, param)
            return values.astype("<f4").tobytes()

    def instanceValues(self, key, param) -> bytes:
        """
        Every instance's value of a per-instance component parameter (like ``pdens``) at one node.

        Laid out for the browser as little-endian: the component count ``n`` (int32), where each
        component's instances start (``n + 1`` int32, components in the frame's order), then the
        values (float32), each component's in the order of its positions.
        """
        with self._lock:
            topo = self.topologyOf(key)
            group = self._group(key)
            typeNames = topo.componentTypes
            indexInData = topo.indexInData[topo.componentRows]
            perComponent = [None] * len(typeNames)
            for typeName in np.unique(typeNames):
                if typeName not in group or param not in group[typeName]:
                    continue
                values = perObject(group[typeName][param])
                if values is None:
                    continue
                for comp in np.flatnonzero(typeNames == typeName):
                    index = int(indexInData[comp])
                    if index < len(values):
                        perComponent[comp] = values[index]
            counts = [0 if v is None else len(v) for v in perComponent]
            offsets = np.concatenate([[0], np.cumsum(counts)]).astype("<i4")
            present = [v for v in perComponent if v is not None]
            values = np.concatenate(present).astype("<f4") if present else np.zeros(0, dtype="<f4")
            return np.int32(len(perComponent)).astype("<i4").tobytes() + offsets.tobytes() + values.tobytes()

    def componentHistory(self, param, nuclide, componentName, blocks, aggregate="max"):
        """One component's value at every node, for the history chart.

        Parameters
        ----------
        blocks : list of int
            The block to look in at each node, in that node's canonical order, or -1 where there is
            none. The browser works these out, since it decides whether a selection follows an
            assembly or a position.
        componentName : str
            Which of the block's components. Empty means all of them, combined by ``aggregate``.
        """
        keys = self.stepKeys()
        if len(blocks) != len(keys):
            raise ResultsError(f"Expected a block for each of the {len(keys)} nodes, got {len(blocks)}.")
        out = []
        combine = {"min": np.nanmin, "mean": np.nanmean}.get(aggregate, np.nanmax)
        for key, block in zip(keys, blocks):
            block = int(block)
            with self._lock:
                topo = self.topologyOf(key)
                if not 0 <= block < len(topo.blockRows):
                    out.append(None)
                    continue
                start = int(topo.componentStart[block])
                stop = int(topo.componentStart[block + 1])
                picks = np.arange(start, stop)
                if componentName:
                    picks = picks[topo.componentNames[start:stop] == componentName]
                if not len(picks):
                    out.append(None)
                    continue
                group = self._group(key)
                typeNames = topo.componentTypes[picks]
                indexInData = topo.indexInData[topo.componentRows[picks]]
                if nuclide:
                    values = self._componentDensities(group, typeNames, indexInData, nuclide)
                else:
                    values = self._gather(group, typeNames, indexInData, param)
            finite = values[np.isfinite(values)]
            out.append(float(combine(finite)) if finite.size else None)
        return out

    def componentRange(self, param, nuclide=None):
        """The smallest and largest value a component parameter takes at any node, for a fixed scale.

        Component values travel one node at a time, so the browser cannot work this out itself
        without fetching everything first.
        """
        cacheKey = ("componentRange", param, nuclide)
        with self._lock:
            if cacheKey in self._series:
                return self._series[cacheKey]
        lo, hi, smallestPositive = np.inf, -np.inf, np.inf
        for key in self.stepKeys():
            values = np.frombuffer(self.componentValues(key, param, nuclide), dtype="<f4")
            finite = values[np.isfinite(values)]
            if finite.size:
                lo, hi = min(lo, float(finite.min())), max(hi, float(finite.max()))
                positive = finite[finite > 0]
                if positive.size:
                    smallestPositive = min(smallestPositive, float(positive.min()))
        result = {
            "min": lo if np.isfinite(lo) else None,
            "max": hi if np.isfinite(hi) else None,
            "minPositive": smallestPositive if np.isfinite(smallestPositive) else None,
        }
        with self._lock:
            self._series[cacheKey] = result
        return result

    def _componentDensities(self, group, typeNames, indexInData, nuclide):
        target = nuclide.encode()
        values = np.full(len(typeNames), np.nan, dtype=np.float64)
        for typeName in np.unique(typeNames):
            if typeName not in group or "numberDensities" not in group[typeName]:
                continue
            sub = group[typeName]
            densities = sub["numberDensities"]
            names = sub["nuclides"][()]
            data = densities[()]
            count = len(np.flatnonzero(typeNames == typeName))
            perObject = np.zeros(max(count, int(indexInData[typeNames == typeName].max()) + 1))
            if data.ndim == 2:
                hits = names == target
                perObject[: data.shape[0]] = np.where(hits, data, 0.0).sum(axis=1)
            else:
                owner = jaggedOwners(densities)
                if not len(owner):
                    continue
                hits = names.ravel() == target
                np.add.at(perObject, owner[hits], data[hits])
            mask = typeNames == typeName
            values[mask] = perObject[indexInData[mask]]
        return values

    # -- component shapes -------------------------------------------------------------------------

    def designs(self, topologyId):
        """What is inside each block, as drawable shapes, shared between blocks built alike.

        Returns ``designs`` (each a list of shapes, in the block's component order) and
        ``blockDesign`` (which design each block in canonical order uses). A core of six thousand
        blocks usually has a few dozen distinct insides, so this stays small even when the pin
        count is not. Shuffling moves blocks but does not change what is in them, so this is worked
        out once per topology.
        """
        with self._lock:
            topo = self._topologyById(topologyId)
            if topo.designs is not None:
                return topo.designs
            # Renaming assemblies does not change what is in their blocks, so share the work.
            designKey = topo.structureKey + hashlib.blake2b(topo.componentNames.tobytes(), digest_size=8).hexdigest()
            if designKey in self._designCache:
                topo.designs = dict(self._designCache[designKey], topology=topo.id)
                return topo.designs
            key = topo.firstStep
            group = self._group(key)
            location = group["layout/location"][()]
            grids_ = self._gridParams(key)
            planar = {index: _planarGeometry(grid) for index, grid in enumerate(grids_)}

            rows = topo.componentRows
            typeNames = topo.componentTypes
            indexInData = topo.indexInData[rows]
            dimNames = sorted({d for names in _SHAPE_DIMS.values() for d in names} | {"mult"})
            dims = {name: self._gather(group, typeNames, indexInData, name) for name in dimNames}
            materials = (
                _decode(group["layout/material"][()][rows]) if "material" in group["layout"] else [""] * len(rows)
            )
            flags = self._flagsOf(group, typeNames, indexInData)
            blockOfComp = topo.componentBlock

            # One id per distinct component, then one design per distinct list of component ids.
            compIds, compIndex, representative = np.zeros(len(rows), dtype=np.int64), {}, []
            for comp, row in enumerate(rows.tolist()):
                gridIndex = topo.blockGrid[blockOfComp[comp]]
                gridIndex = int(gridIndex) if gridIndex is not None and 0 <= int(gridIndex) < len(grids_) else -1
                start, count = int(topo.locStart[row]), int(topo.locCount[row])
                where = None
                if gridIndex >= 0 and not topo.locIsNone[row]:
                    cells = location[start : start + count, :2]
                    if count > 1 or np.any(cells):
                        where = hashlib.blake2b(cells.tobytes(), digest_size=8).digest()
                typeName = typeNames[comp]
                shapeDims = tuple(
                    round(float(dims[name][comp]), 6) for name in _SHAPE_DIMS.get(typeName, ()) + ("mult",)
                )
                signature = (
                    typeName,
                    topo.componentNames[comp],
                    materials[comp],
                    flags[comp],
                    shapeDims,
                    gridIndex,
                    where,
                )
                if signature not in compIndex:
                    compIndex[signature] = len(representative)
                    representative.append((comp, gridIndex, start, count, where is not None))
                compIds[comp] = compIndex[signature]

            shapesById = []
            for comp, gridIndex, start, count, placed in representative:
                typeName = typeNames[comp]
                shape = {
                    "type": typeName,
                    "name": topo.componentNames[comp],
                    "material": materials[comp],
                    "flags": flags[comp],
                }
                for name in _SHAPE_DIMS.get(typeName, ()) + ("mult",):
                    value = dims[name][comp]
                    if np.isfinite(value):
                        shape[name] = round(float(value), 6)
                if placed:
                    geometry = planar[gridIndex]
                    cells = location[start : start + count, :2]
                    xy = np.outer(cells[:, 0], geometry["iStep"]) + np.outer(cells[:, 1], geometry["jStep"])
                    shape["at"] = np.round(xy, 4).ravel().tolist()
                    # The lattice indices of each site, which is how ARMI addresses a pin.
                    shape["ij"] = np.rint(cells).astype(int).ravel().tolist()
                    if geometry["shape"] == "hex":
                        shape["pitch"] = geometry["pitch"]
                shapesById.append(shape)

            designs, designIndex, blockDesign = [], {}, []
            starts = np.concatenate([[0], np.cumsum(topo.componentsPerBlock)]).tolist()
            for block in range(len(topo.blockRows)):
                signature = compIds[starts[block] : starts[block + 1]].tobytes()
                if signature not in designIndex:
                    designIndex[signature] = len(designs)
                    designs.append([shapesById[i] for i in compIds[starts[block] : starts[block + 1]].tolist()])
                blockDesign.append(designIndex[signature])

            topo.designs = {"topology": topo.id, "designs": designs, "blockDesign": blockDesign}
            self._designCache[designKey] = topo.designs
            return topo.designs

    def _flagsOf(self, group, typeNames, indexInData):
        flags = [""] * len(typeNames)
        for typeName in np.unique(typeNames):
            if typeName not in group or "flags" not in group[typeName]:
                continue
            dataset = group[typeName]["flags"]
            order = dataset.attrs.get("flag_order")
            if order is None:
                continue
            decoded = decodeFlags(dataset[()], order)
            for position in np.flatnonzero(typeNames == typeName):
                index = indexInData[position]
                if index < len(decoded):
                    flags[position] = decoded[index]
        return flags

    # -- one block in detail ----------------------------------------------------------------------

    def blockDetail(self, key, block):
        """The scalar parameters of one block at one node, for the inspector."""
        with self._lock:
            topo = self.topologyOf(key)
            if not 0 <= block < len(topo.blockRows):
                raise ResultsError(f"There is no block {block} at {key}.")
            group = self._group(key)
            typeName = topo.blockTypes[block]
            index = int(topo.indexInData[topo.blockRows[block]])
            values = {}
            if typeName in group:
                for name, dataset in group[typeName].items():
                    if not isinstance(dataset, h5py.Dataset) or dataset.ndim != 1 or dataset.dtype.kind not in "fiu":
                        continue
                    if dataset.attrs.get("specialFormatting", False) and not dataset.attrs.get("nones", False):
                        continue
                    if index < dataset.shape[0]:
                        value = float(dataset[index])
                        if np.isfinite(value) and abs(value) < 1e300:
                            values[name] = value
            if "flags" in group.get(typeName, {}):
                dataset = group[typeName]["flags"]
                order = dataset.attrs.get("flag_order")
                if order is not None:
                    values["flags"] = decodeFlags(dataset[index : index + 1], order)[0]
            return {"name": str(topo.blockNames[block]), "type": typeName, "params": values}


# -- helpers ----------------------------------------------------------------------------------------


def _allSubclasses(cls):
    found = set()
    for sub in cls.__subclasses__():
        found.add(sub)
        found |= _allSubclasses(sub)
    return found


def _planarGeometry(grid):
    """The two steps that carry ``(i, j)`` to ``(x, y)``, and what shape each cell is."""
    steps = np.zeros((3, 3))
    raw = np.atleast_2d(grid["unitSteps"])
    steps[: raw.shape[0], : raw.shape[1]] = raw[:3, :3]
    iStep = [float(steps[0][0]), float(steps[1][0])]
    jStep = [float(steps[0][1]), float(steps[1][1])]
    isHex = "Hex" in grid["type"] or grid.get("geomType") == "hex"
    pitch = float(np.hypot(*jStep)) if isHex else None
    if isHex:
        # Hex cells put a flat face toward each neighbour, so a corner sits 30 degrees off one.
        angle = float(np.degrees(np.arctan2(jStep[1], jStep[0]))) + 30.0
    else:
        angle = 0.0
    return {
        "shape": "hex" if isHex else "rect",
        "iStep": iStep,
        "jStep": jStep,
        "pitch": pitch if isHex else [abs(iStep[0]) or 1.0, abs(jStep[1]) or 1.0],
        "cornerAngle": angle,
        "symmetry": grid.get("symmetry") or "",
        "geomType": grid.get("geomType") or "",
    }


def _describe(name, dataset):
    """What the viewer needs to know about one parameter dataset, or None if it cannot be shown."""
    if dataset.dtype.kind not in "fiu":
        return None
    attrs = dataset.attrs
    if "serializerName" in attrs or attrs.get("jagged", False) or attrs.get("dict", False):
        return None
    shape = dataset.shape
    entry = {"name": name, "shape": list(shape[1:])}
    if _NUCLIDE_PARAM.match(name):
        entry["group"] = "Number densities"
        entry["units"] = "atoms/bn-cm"
    return entry


def _annotate(level, found):
    """Add units and descriptions from ARMI's own parameter definitions, where it has them."""
    try:
        from armi.reactor import assemblies, blocks, components, cores, reactors

        owner = {
            "block": blocks.Block,
            "assembly": assemblies.Assembly,
            "component": components.Component,
            "core": cores.Core,
            "reactor": reactors.Reactor,
        }[level]
        pDefs = owner.pDefs
    except Exception:
        return
    for name, entry in found.items():
        try:
            definition = pDefs[name]
        except Exception:
            continue
        entry.setdefault("units", definition.units)
        if definition.description:
            entry["description"] = definition.description
        category = next(iter(sorted(definition.categories)), None) if definition.categories else None
        if category and "group" not in entry:
            entry["category"] = category
