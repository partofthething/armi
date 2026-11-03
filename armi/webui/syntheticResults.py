"""
A made-up results database, big enough to find out whether the results viewer keeps up.

Real ARMI runs that span decades take real compute, and the viewer's job is to stay quick on files
of that size. This builds one ARMI can actually read without running any physics: the reactor is
built from the ``anl-afci-177`` test inputs, its blocks are cut finer, ARMI writes the first time
node the ordinary way, and every later node is a copy of that one with the numbers changed and the
fuel shuffled. The parameter values follow plausible shapes (a cosine power profile, burnup that
accumulates where the power is, fuel that depletes) but are not the answer to anything.

From a shell::

    $ python -m armi.webui.syntheticResults big.h5 --years 60 --nodes-per-year 4

The defaults give a full core of 313 assemblies with 20 blocks each, which is about 6,000 blocks
and 40,000 components per time node.
"""

import argparse
import math
import os
import pathlib
import time

import h5py
import numpy as np

from armi import configure, isConfigured, runLog

INPUTS = pathlib.Path(__file__).parents[1] / "testing" / "reactors" / "anl-afci-177"

# How finely each block of the source design is cut, by its height in cm. The fuel slugs in the
# test inputs are 20 cm and the plenum and the one-piece reflector assemblies are about 200 to 300
# cm; this takes every assembly to about 20 blocks.
_SPLIT_RULES = ((100.0, 10), (150.0, 4), (19.0, 3))

# Block parameters this rewrites at every node. Everything else keeps its first-node value.
_BLOCK_PARAMS = ("pdens", "power", "percentBu", "fastFlux", "THcornTemp", "THedgeTemp", "residence")


def buildReactor(fullCore=True, split=True):
    """Build the test reactor this database describes, at its starting state."""
    if not isConfigured():
        configure(permissive=True)

    from armi.reactor.converters.geometryConverters import ThirdCoreHexToFullCoreChanger
    from armi.testing import loadTestReactor

    o, r = loadTestReactor(str(INPUTS), inputFileName="anl-afci-177.yaml", useCache=False)
    if fullCore:
        ThirdCoreHexToFullCoreChanger(o.cs).convert(r)
    if split:
        for assembly in list(r.core):
            _splitBlocks(assembly)
        r.core.updateAxialMesh()
    return o.cs, r


def _splitBlocks(assembly):
    import copy

    pieces = []
    for block in list(assembly):
        height = block.getHeight()
        parts = next((count for limit, count in _SPLIT_RULES if height > limit), 1)
        for part in range(parts):
            piece = block if part == 0 else copy.deepcopy(block)
            piece.setHeight(height / parts)
            pieces.append(piece)
    assembly.removeAll()
    for piece in pieces:
        assembly.add(piece)
    assembly.reestablishBlockOrder()
    assembly.calculateZCoords()


def writeSyntheticDatabase(
    path, years=60, nodesPerYear=4, fullCore=True, split=True, seed=7, componentTemperatures=True
):
    """Write the database and return its path.

    Parameters
    ----------
    path : str
        Where the ``.h5`` goes. An existing file is replaced.
    years : int
        Operating years. Each year is one cycle.
    nodesPerYear : int
        Time nodes per cycle, so ``nodesPerYear - 1`` burn steps.
    fullCore : bool
        Unfold the third-core test inputs to a full core.
    split : bool
        Cut blocks finer, to about 20 per assembly.
    componentTemperatures : bool
        Give components temperatures that follow the power. ARMI recomputes a component's volume
        from its temperature when it loads a reactor, and this does not change the stored volumes
        to match, so with this on a reactor loaded from the file weighs slightly differently from
        the numbers in it. Turn it off when comparing against ``Database.load``.
    seed : int
        For the shuffling, so the same arguments make the same file.
    """
    from armi.bookkeeping.db.database import Database

    started = time.time()
    path = pathlib.Path(path)
    if path.exists():
        path.unlink()

    cs, r = buildReactor(fullCore=fullCore, split=split)
    blocks = [b for a in r.core for b in a]
    for b in blocks:
        for name in _BLOCK_PARAMS:
            b.p[name] = 0.0
    r.p.cycle, r.p.timeNode, r.p.time = 0, 0, 0.0
    r.p.cycleLength = 365.25

    # The stored settings describe the history actually written, so anything reading them (cycle
    # lengths, what reloads there were) sees the same plant the numbers do.
    cs = cs.modified(newSettings={"nCycles": years, "cycleLength": 365.25, "burnSteps": max(nodesPerYear - 1, 1)})
    db = Database(str(path), "w")
    with db:
        db.writeInputsToDB(cs)
        db.writeToDB(r)
    runLog.important(f"Wrote the first node in {time.time() - started:.1f} s; cloning the rest.")

    with h5py.File(path, "r+") as h5:
        _cloneTimeSteps(h5, years, nodesPerYear, seed, componentTemperatures)
    runLog.important(f"Wrote {path} ({path.stat().st_size / 1e6:.0f} MB) in {time.time() - started:.1f} s.")
    return path


# -- the made-up physics --------------------------------------------------------------------------


class _Model:
    """Everything that stays put between nodes, worked out once from the first node."""

    def __init__(self, group):
        layout = group["layout"]
        self.types = layout["type"][:]
        self.numChildren = layout["numChildren"][:]
        self.locationType = layout["locationType"][:]
        self.location = layout["location"][:]

        # The layout is depth first, so a running stack of open parents finds each row's parent.
        rows = len(self.types)
        parent = np.full(rows, -1)
        remaining = []
        for row in range(rows):
            while remaining and remaining[-1][1] == 0:
                remaining.pop()
            if remaining:
                parent[row] = remaining[-1][0]
                remaining[-1][1] -= 1
            remaining.append([row, int(self.numChildren[row])])
        self.parent = parent

        # Location rows: one per object, except multi-index locations that take one per index.
        extra = np.array([int(t[2:]) - 1 if t.startswith(b"M:") else 0 for t in self.locationType])
        self.locRow = np.arange(rows) + np.concatenate([[0], np.cumsum(extra)[:-1]])

        self.assemblyRows = np.flatnonzero(self.types == b"HexAssembly")
        coreRow = int(np.flatnonzero(self.types == b"Core")[0])
        self.assemblyRows = self.assemblyRows[self.parent[self.assemblyRows] == coreRow]
        self.blockRows = np.flatnonzero(self.types == b"HexBlock")
        self.blockAssembly = self.parent[self.blockRows]

        steps = group["layout/grids/0/unitSteps"][:]
        ij = self.location[self.locRow[self.assemblyRows], :2]
        self.assemblyXY = ij @ np.array([[steps[0][0], steps[1][0]], [steps[0][1], steps[1][1]]])
        self.assemblyIJ = ij.copy()
        self.radius = float(np.max(np.hypot(*self.assemblyXY.T))) or 1.0

        blockGroup = group["HexBlock"]
        self.zMid = blockGroup["z"][:]
        self.height = blockGroup["height"][:]
        from armi.webui.results import decodeFlags, jaggedOwners

        flags = decodeFlags(blockGroup["flags"][()], blockGroup["flags"].attrs["flag_order"])
        self.isFuelBlock = np.array(["FUEL" in each.split() for each in flags])
        fuelZ = self.zMid[self.isFuelBlock]
        self.fuelBottom = float(fuelZ.min() - 10) if fuelZ.size else 0.0
        self.fuelTop = float(fuelZ.max() + 10) if fuelZ.size else 1.0

        assemblyIndex = {row: i for i, row in enumerate(self.assemblyRows)}
        self.blockToAssembly = np.array([assemblyIndex.get(int(row), -1) for row in self.blockAssembly])
        self.isFuelAssembly = np.zeros(len(self.assemblyRows), bool)
        for block, assembly in enumerate(self.blockToAssembly):
            if assembly >= 0 and self.isFuelBlock[block]:
                self.isFuelAssembly[assembly] = True

        # Component bookkeeping, for depleting the fuel's number densities.
        self.components = {}
        for name in group:
            sub = group[name]
            if isinstance(sub, h5py.Group) and "numberDensities" in sub and "nuclides" in sub:
                densities = sub["numberDensities"]
                rows = np.flatnonzero(self.types == name.encode())
                if densities.ndim == 2:
                    # Every component of this type carries the same nuclides, so it is a plain table.
                    owner = np.repeat(np.arange(densities.shape[0]), densities.shape[1])
                else:
                    owner = jaggedOwners(densities)
                blockOf = self.parent[rows]
                self.components[name] = {
                    "owner": owner,
                    "shape": densities.shape,
                    "nuclides": sub["nuclides"][:].ravel(),
                    "start": densities[:].ravel(),
                    "blockRow": blockOf,
                    "temperature": sub["temperatureInC"][:] if "temperatureInC" in sub else None,
                }
        blockIndexOfRow = {int(row): i for i, row in enumerate(self.blockRows)}
        for info in self.components.values():
            info["block"] = np.array([blockIndexOfRow.get(int(row), -1) for row in info["blockRow"]])


def _cloneTimeSteps(h5, years, nodesPerYear, seed, componentTemperatures=True):
    rng = np.random.default_rng(seed)
    first = h5["c00n00"]
    model = _Model(first)

    nAssem = len(model.assemblyRows)
    # Where each assembly sits, as an index into the starting positions. Shuffling permutes this.
    placement = np.arange(nAssem)
    burnup = np.zeros(len(model.blockRows))
    residence = np.zeros(len(model.blockRows))
    dtYears = 1.0 / nodesPerYear
    names = first["layout/name"][()].copy()
    assemNum = first["HexAssembly/assemNum"][()].copy() if "assemNum" in first["HexAssembly"] else None
    nextNumber = int(assemNum.max()) + 1 if assemNum is not None and len(assemNum) else 10000

    for cycle in range(years):
        if cycle:
            placement, discharged = _shuffle(model, placement, burnup, residence, rng)
            # Fresh fuel is a new assembly, as it is when fuel management loads it: a new name and
            # number for it and its blocks, which is how anything reading the history tells it apart
            # from the fuel it replaced.
            for assembly in discharged:
                nextNumber = _rename(model, names, assemNum, assembly, nextNumber)
        for node in range(nodesPerYear):
            name = f"c{cycle:02d}n{node:02d}"
            if name != "c00n00":
                h5.copy(first, name)
            group = h5[name]
            group.attrs["cycle"] = cycle
            group.attrs["timeNode"] = node
            years_ = cycle + node * dtYears

            # Positions this node.
            xy = model.assemblyXY[placement]
            ij = model.assemblyIJ[placement]
            location = model.location.copy()
            location[model.locRow[model.assemblyRows], :2] = ij
            blockAssembly = model.blockToAssembly
            inCore = blockAssembly >= 0
            location[model.locRow[model.blockRows[inCore]], :2] = ij[blockAssembly[inCore]]
            group["layout/location"][...] = location
            group["layout/name"][...] = names
            if assemNum is not None:
                group["HexAssembly/assemNum"][...] = assemNum

            radial = np.zeros(len(model.blockRows))
            radial[inCore] = np.hypot(*xy[blockAssembly[inCore]].T) / (model.radius * 1.08)
            axial = (model.zMid - model.fuelBottom) / (model.fuelTop - model.fuelBottom)
            shape = np.cos(0.5 * np.pi * radial) * np.clip(np.sin(np.pi * np.clip(axial, 0, 1)), 0.05, None)
            depletion = 1.0 - 0.35 * np.clip(burnup / 20.0, 0, 1)
            swing = 1.0 + 0.04 * math.sin(2 * math.pi * years_)
            pdens = np.where(model.isFuelBlock, 300.0 * shape * depletion * swing, 3.0 * shape)
            pdens *= 1.0 + 0.02 * rng.standard_normal(len(pdens))
            pdens = np.clip(pdens, 0.0, None)

            if node:
                burnup += pdens * model.isFuelBlock * dtYears * 0.012
                residence += dtYears * 365.25 * model.isFuelBlock
            coolant = 355.0 + 155.0 * np.clip(axial, 0, 1) * (0.4 + shape)

            values = {
                "pdens": pdens,
                "power": pdens * model.height * 255.0,
                "percentBu": burnup,
                "fastFlux": 2.5e15 * shape * swing,
                "THcornTemp": coolant,
                "THedgeTemp": coolant + 4.0 * shape,
                "residence": residence,
            }
            for key, value in values.items():
                if key in group["HexBlock"]:
                    group["HexBlock"][key][...] = value

            fuelTemp = coolant + 260.0 * pdens / 300.0
            _depleteComponents(group, model, burnup, coolant, fuelTemp, componentTemperatures)
            _updateBlockDensities(group, model, burnup)

            reactor = group["Reactor"]
            for key, value in (("cycle", cycle), ("timeNode", node), ("time", years_)):
                if key in reactor:
                    reactor[key][...] = value
                else:
                    reactor.create_dataset(key, data=np.array([value]))
        runLog.info(f"Wrote cycle {cycle}")


def _shuffle(model, placement, burnup, residence, rng):
    """Move fuel around: the most burnt third out, fresh fuel in at the edge, the rest inward."""
    fuel = np.flatnonzero(model.isFuelAssembly)
    blockAssembly = model.blockToAssembly
    assemblyBurnup = np.zeros(len(model.assemblyRows))
    np.add.at(assemblyBurnup, blockAssembly[blockAssembly >= 0], burnup[blockAssembly >= 0])

    # Discharge the most burnt third and recharge them fresh.
    order = fuel[np.argsort(-assemblyBurnup[fuel])]
    discharged = order[: len(order) // 3]
    for assembly in discharged:
        mask = blockAssembly == assembly
        burnup[mask] = 0.0
        residence[mask] = 0.0

    # Fuel positions, from the centre out. Burnt fuel goes to the edge and fresh fuel to the middle
    # ring band, with some noise so no two cycles look the same.
    slots = placement[fuel]
    radii = np.hypot(*model.assemblyXY[slots].T) + rng.normal(0, 4.0, len(slots))
    slotsByRadius = slots[np.argsort(radii)]
    assemblyBurnup[discharged] = assemblyBurnup[fuel].mean() * 0.5 + rng.random(len(discharged))
    byBurnup = fuel[np.argsort(assemblyBurnup[fuel] + rng.normal(0, 1.0, len(fuel)))]
    newPlacement = placement.copy()
    newPlacement[byBurnup] = slotsByRadius
    return newPlacement, discharged


def _rename(model, names, assemNum, assembly, number):
    """Give an assembly and its blocks the names of a new assembly with this number; return the next number."""
    width = names.dtype.itemsize
    row = model.assemblyRows[assembly]
    names[row] = f"A{number:04d}".encode()[:width]
    for block in np.flatnonzero(model.blockToAssembly == assembly):
        old = names[model.blockRows[block]].decode()
        suffix = old.split("-", 1)[1] if "-" in old else str(block)
        names[model.blockRows[block]] = f"B{number:04d}-{suffix}".encode()[:width]
    if assemNum is not None:
        assemNum[assembly] = number
    return number + 1


def _depleteComponents(group, model, burnup, coolant, fuelTemp, temperatures=True):
    for name, info in model.components.items():
        sub = group[name]
        block = info["block"]
        valid = block >= 0
        if temperatures and info["temperature"] is not None:
            rowsInType = len(info["temperature"])
            temperature = np.array(info["temperature"], dtype=float)
            isFuel = np.zeros(rowsInType, bool)
            ownerHasU = np.zeros(rowsInType, bool)
            uMask = np.char.startswith(info["nuclides"], b"U2")
            ownerHasU[np.unique(info["owner"][uMask])] = True
            isFuel = ownerHasU
            temperature[valid] = coolant[block[valid]]
            temperature[valid & isFuel] = fuelTemp[block[valid & isFuel]]
            sub["temperatureInC"][...] = temperature
        owner = info["owner"]
        entryBlock = block[owner]
        entryBurnup = np.where(entryBlock >= 0, burnup[np.clip(entryBlock, 0, None)], 0.0)
        fraction = np.clip(entryBurnup / 100.0, 0, 0.5)
        nuclides = info["nuclides"]
        densities = info["start"].copy()
        densities[nuclides == b"U235"] *= 1 - 1.6 * fraction[nuclides == b"U235"]
        densities[nuclides == b"U238"] *= 1 - 0.3 * fraction[nuclides == b"U238"]
        pu = nuclides == b"PU239"
        densities[pu] = densities[pu] + 0.004 * fraction[pu]
        sub["numberDensities"][...] = np.clip(densities, 0, None).reshape(info["shape"])


def _updateBlockDensities(group, model, burnup):
    blockGroup = group["HexBlock"]
    fraction = np.clip(burnup / 100.0, 0, 0.5)
    if not hasattr(model, "blockStart"):
        model.blockStart = {key: blockGroup[key][:] for key in ("nU235", "nU238", "nPu239") if key in blockGroup}
    for key, start in model.blockStart.items():
        if key == "nU235":
            value = start * (1 - 1.6 * fraction)
        elif key == "nU238":
            value = start * (1 - 0.3 * fraction)
        else:
            value = start + 0.004 * fraction * (model.blockStart.get("nU238", 0) > 0)
        blockGroup[key][...] = value


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("path", help="Where to write the .h5 file")
    parser.add_argument("--years", type=int, default=60)
    parser.add_argument("--nodes-per-year", type=int, default=4)
    parser.add_argument("--third-core", action="store_true", help="Leave the core in thirds")
    parser.add_argument("--coarse", action="store_true", help="Keep the input's own blocks")
    args = parser.parse_args(argv)
    os.makedirs(os.path.dirname(os.path.abspath(args.path)), exist_ok=True)
    writeSyntheticDatabase(
        args.path,
        years=args.years,
        nodesPerYear=args.nodes_per_year,
        fullCore=not args.third_core,
        split=not args.coarse,
    )


if __name__ == "__main__":
    main()
