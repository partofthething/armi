"""
A run's fuel cycle cost, worked out from its database.

The economics plugin (:py:mod:`armi.physics.economics`) costs a live core: it weighs each component's uranium and
heavy metal, groups the fuel by form and enrichment, and prices it. This module weighs the same components from the
numbers the run stored, so a run's fuel cycle cost can be looked at afterwards, at any prices, without rebuilding a
reactor and whether or not the plugin was on while it ran. Everything past the weighing (enrichment, prices, lead
times, levelizing) is the plugin's own code.

The fuel is weighed the way the plugin weighs it:

- mass is number density times volume times atomic weight, the component's volume divided by its block's symmetry
  factor (a third of the centre assembly in a third-core model) and the total scaled up to the full core;
- a component is fuel if it holds heavy metal; it is TRISO if its block holds silicon and at least as much carbon as
  heavy metal, an oxide if it holds oxygen past ``OXIDE_O_TO_HM`` of its heavy metal, and metal otherwise;
- reloads are the assemblies that first appear in the core at the start of a cycle with no burnup, when the case
  takes its reloads from the run (``econReloads``), and a fraction of the initial core otherwise.
"""

import numpy as np

from armi import runLog
from armi.physics.economics import economicsInterface, fuelCost, summary
from armi.physics.economics import settings as econSettings
from armi.webui.results import ResultsError, jaggedOwners

#: Settings the browser may change, with how to show them: label, units, and the kind of control.
EDITABLE = [
    ("Prices", econSettings.CONF_ECON_ORE_PRICE, "Natural uranium", "$/lb U3O8", "number"),
    ("Prices", econSettings.CONF_ECON_CONVERSION_COST, "Conversion", "$/kgU", "number"),
    ("Prices", econSettings.CONF_ECON_SWU_COST, "Enrichment", "$/SWU", "number"),
    ("Prices", econSettings.CONF_ECON_FAB_COST_UO2, "Fabrication, UO2", "$/kgHM", "number"),
    ("Prices", econSettings.CONF_ECON_FAB_COST_UMETAL, "Fabrication, U-metal", "$/kgHM", "number"),
    ("Prices", econSettings.CONF_ECON_FAB_COST_TRISO, "Fabrication, TRISO", "$/kgHM", "number"),
    ("Prices", econSettings.CONF_ECON_BACK_END_COST, "Spent fuel", "$/MWh", "number"),
    ("Enrichment", econSettings.CONF_ECON_TAILS_ASSAY, "Tails assay", "wt fraction", "number"),
    ("Enrichment", econSettings.CONF_ECON_FEED_ASSAY, "Feed assay", "wt fraction", "number"),
    ("Enrichment", econSettings.CONF_ECON_CONVERSION_LOSS, "Conversion loss", "fraction", "number"),
    ("Plant", econSettings.CONF_ECON_THERMAL_EFFICIENCY, "Net thermal efficiency", "fraction", "number"),
    ("Plant", econSettings.CONF_ECON_DISCOUNT_RATE, "Discount rate", "fraction, real", "number"),
    ("Plant", econSettings.CONF_ECON_FUEL_FORM, "Fuel form", "", "choice"),
    ("Reloads", econSettings.CONF_ECON_RELOADS, "Reloads from", "", "choice"),
    ("Reloads", econSettings.CONF_ECON_RELOAD_FRACTION, "Projected reload fraction", "of the core", "number"),
    ("Lead times", econSettings.CONF_ECON_LEAD_ORE, "Natural uranium", "y before loading", "number"),
    ("Lead times", econSettings.CONF_ECON_LEAD_CONVERSION, "Conversion", "y before loading", "number"),
    ("Lead times", econSettings.CONF_ECON_LEAD_ENRICHMENT, "Enrichment", "y before loading", "number"),
    ("Lead times", econSettings.CONF_ECON_LEAD_FABRICATION, "Fabrication", "y before loading", "number"),
]
_EDITABLE_NAMES = {name for _, name, _, _, _ in EDITABLE}


class FuelCycleFromResults:
    """The fuel cycle cost of the run behind a :py:class:`~armi.webui.results.ResultsDatabase`.

    The fuel is weighed once; changing a price only redoes the arithmetic.
    """

    def __init__(self, results):
        self.results = results
        self.cs = self._loadSettings()
        self._charges = {}

    def _loadSettings(self):
        from armi.bookkeeping.db.database import Database

        try:
            with Database(str(self.results.path), "r") as db:
                return db.loadCS(handleInvalids=False)
        except Exception as ee:
            raise ResultsError(f"Could not read the case settings stored in {self.results.path.name}: {ee}") from ee

    # -- settings ---------------------------------------------------------------------------------

    def settingsWith(self, overrides=None):
        """The case settings with the browser's changes, refusing anything that is not an economics input."""
        overrides = {key: value for key, value in (overrides or {}).items() if value is not None}
        unknown = set(overrides) - _EDITABLE_NAMES
        if unknown:
            raise ResultsError(f"Cannot change {', '.join(sorted(unknown))} here.")
        if not overrides:
            return self.cs
        try:
            return self.cs.modified(newSettings=overrides)
        except Exception as ee:
            raise ResultsError(f"Those settings do not work: {ee}") from ee

    def editable(self, cs):
        """The inputs the browser can change, with their case and current values."""
        out = []
        for section, name, label, units, kind in EDITABLE:
            setting = self.cs.getSetting(name)
            entry = {
                "section": section,
                "name": name,
                "label": label,
                "units": units,
                "kind": kind,
                "value": cs[name],
                "caseValue": self.cs[name],
                "description": setting.description,
            }
            if kind == "choice":
                entry["options"] = list(setting.options or [])
            out.append(entry)
        return out

    # -- weighing the fuel ------------------------------------------------------------------------

    def inventory(self, key, form, assemblies=None):
        """Heavy metal at a node grouped by fuel form and enrichment, in kg, full core.

        Parameters
        ----------
        key : str
            The time node.
        form : str
            One fuel form for everything, or ``"auto"`` to classify each component.
        assemblies : iterable of int, optional
            Only these assemblies, as indices into the node's topology.
        """
        db = self.results
        topo = db.topologyOf(key)
        masses = self._componentMasses(key)

        blockFactor = self._blockSymmetryFactors(key)
        compBlock = topo.componentBlock
        scale = db.powerMultiplier() / 1000.0 / blockFactor[compBlock]
        hm = masses["hm"] * scale
        u = masses["u"] * scale
        u235 = masses["u235"] * scale
        oxygen = masses["o"] * scale

        keep = hm > 0.0
        if assemblies is not None:
            wanted = np.zeros(len(topo.assemblyRows), dtype=bool)
            wanted[list(assemblies)] = True
            keep &= wanted[topo.blockAssembly[compBlock]]

        if form == "auto":
            blocks = len(topo.blockRows)
            silicon = np.bincount(compBlock, weights=masses["si"], minlength=blocks)
            carbon = np.bincount(compBlock, weights=masses["c"], minlength=blocks)
            blockHm = np.bincount(compBlock, weights=masses["hm"], minlength=blocks)
            triso = (silicon > 0.0) & (carbon >= blockHm)
            forms = np.where(
                triso[compBlock],
                "TRISO",
                np.where(oxygen > fuelCost.OXIDE_O_TO_HM * hm, "UO2", "U-metal"),
            )
        else:
            forms = np.full(len(hm), form)

        groups = {}
        for comp in np.flatnonzero(keep):
            assay = round(u235[comp] / u[comp], fuelCost.ASSAY_DECIMALS) if u[comp] else 0.0
            entry = groups.setdefault((str(forms[comp]), assay), fuelCost.FuelGroup(str(forms[comp])))
            entry.heavyMetal += hm[comp]
            entry.uranium += u[comp]
            entry.u235 += u235[comp]
        return [groups[k] for k in sorted(groups)]

    def _componentMasses(self, key):
        """Grams of heavy metal, uranium, U-235, oxygen, silicon and carbon in every component, before symmetry."""
        cache = self.__dict__.setdefault("_masses", {})
        if key not in cache:
            if len(cache) > 8:
                cache.pop(next(iter(cache)))
            cache[key] = self._weigh(key)
        return cache[key]

    def _weigh(self, key):
        from armi.nucDirectory import nuclideBases
        from armi.utils import units

        db = self.results
        topo = db.topologyOf(key)
        group = db._group(key)
        rows = topo.componentRows
        typeNames = topo.componentTypes
        indexInData = topo.indexInData[rows]
        volume = db._gather(group, typeNames, indexInData, "volume")
        out = {name: np.zeros(len(rows)) for name in ("hm", "u", "u235", "o", "si", "c")}

        for typeName in np.unique(typeNames):
            if typeName not in group or "numberDensities" not in group[typeName]:
                continue
            sub = group[typeName]
            densities = sub["numberDensities"]
            data = densities[()]
            names = sub["nuclides"][()]
            if data.ndim == 2:
                owner = np.repeat(np.arange(data.shape[0]), data.shape[1])
                data = data.ravel()
                names = np.broadcast_to(names, densities.shape).ravel() if names.ndim == 1 else names.ravel()
            else:
                owner = jaggedOwners(densities)
                names = names.ravel()
            if not len(owner):
                continue

            # One lookup per distinct nuclide, then everything is arrays.
            distinct, which = np.unique(names, return_inverse=True)
            weight = np.zeros(len(distinct))
            masks = {name: np.zeros(len(distinct), dtype=bool) for name in out}
            for k, raw in enumerate(distinct):
                nuclide = nuclideBases.byName.get(raw.decode() if isinstance(raw, bytes) else str(raw))
                if nuclide is None:
                    continue
                weight[k] = nuclide.weight
                symbol = getattr(getattr(nuclide, "element", None), "symbol", "") or ""
                symbol = symbol.upper()
                masks["hm"][k] = nuclide.isHeavyMetal()
                masks["u"][k] = symbol == "U"
                masks["u235"][k] = nuclide.name == "U235"
                masks["o"][k] = symbol == "O"
                masks["si"][k] = symbol == "SI"
                masks["c"][k] = symbol == "C"

            # g/cc of each entry; times the component's volume gives grams.
            density = data * weight[which] / units.MOLES_PER_CC_TO_ATOMS_PER_BARN_CM
            mine = np.flatnonzero(typeNames == typeName)
            position = np.full(int(indexInData[mine].max()) + 1, -1)
            position[indexInData[mine]] = mine
            target = position[np.clip(owner, 0, len(position) - 1)]
            valid = (owner < len(position)) & (target >= 0)
            for name, mask in masks.items():
                pick = valid & mask[which]
                if pick.any():
                    np.add.at(out[name], target[pick], density[pick])
        for name in out:
            out[name] *= np.nan_to_num(volume)
        return out

    def _blockSymmetryFactors(self, key):
        """How much of each block is cut off by symmetry lines, as ARMI's hex blocks work it out."""
        db = self.results
        topo = db.topologyOf(key)
        factors = np.ones(len(topo.blockRows))
        grid = db.coreGrid()
        if grid is None:
            return factors
        from armi.reactor import geometry, grids

        symmetry = grid.symmetry
        if not (
            symmetry.domain == geometry.DomainType.THIRD_CORE and symmetry.boundary == geometry.BoundaryType.PERIODIC
        ):
            return factors
        _frame, ij = db.positionsOf(key)
        occupied = {tuple(each) for each in ij.tolist()}
        upperEdge = (-1, 2) in occupied
        for a, (i, j) in enumerate(ij.tolist()):
            if i == 0 and j == 0:
                factor = 3.0
            elif upperEdge and grid.overlapsWhichSymmetryLine((i, j)) in (
                grids.BOUNDARY_0_DEGREES,
                grids.BOUNDARY_120_DEGREES,
            ):
                factor = 2.0
            else:
                continue
            factors[topo.blockStart[a] : topo.blockStart[a + 1]] = factor
        return factors

    # -- the charges ------------------------------------------------------------------------------

    def _firstNodes(self):
        """The first time node of each cycle, in order."""
        first = {}
        for step in self.results.steps:
            if step["label"]:
                continue
            if step["cycle"] not in first or step["node"] < first[step["cycle"]]["node"]:
                first[step["cycle"]] = step
        return [first[c] for c in sorted(first)]

    def reloadsFromRun(self, form, cycles):
        """The fresh fuel the run's fuel management loaded at the start of each later cycle."""
        cacheKey = ("run", form)
        if cacheKey in self._charges:
            return self._charges[cacheKey]
        db = self.results
        firstNodes = self._firstNodes()
        seen = set(db.topologyOf(firstNodes[0]["key"]).assemblyNames) if firstNodes else set()
        charges = []
        for step in firstNodes[1:]:
            key, cycle = step["key"], step["cycle"]
            topo = db.topologyOf(key)
            new = [a for a, name in enumerate(topo.assemblyNames) if name not in seen]
            seen.update(topo.assemblyNames)
            fresh = self._fresh(key, new)
            if not fresh or cycle >= len(cycles):
                if fresh:
                    runLog.warning(f"{key} loads fresh fuel after the last cycle the settings describe; not costed.")
                continue
            groups = self.inventory(key, form, fresh)
            charges.append(fuelCost.FuelCharge(cycles[cycle].start, cycle, groups, f"Reload {cycle}"))
        self._charges[cacheKey] = charges
        return charges

    def _fresh(self, key, assemblies):
        """Which of these assemblies hold heavy metal that has never been irradiated."""
        if not assemblies:
            return []
        db = self.results
        topo = db.topologyOf(key)
        group = db._group(key)
        burnup = db._gather(group, topo.blockTypes, topo.indexInData[topo.blockRows], "percentBu")
        masses = self._componentMasses(key)
        blockHm = np.bincount(topo.componentBlock, weights=masses["hm"], minlength=len(topo.blockRows))
        fresh = []
        for a in assemblies:
            blocks = range(topo.blockStart[a], topo.blockStart[a + 1])
            fuel = [b for b in blocks if blockHm[b] > 0.0]
            if fuel and all(np.nan_to_num(burnup[b]) < fuelCost.FRESH_BURNUP for b in fuel):
                fresh.append(a)
        return fresh

    def cost(self, overrides=None) -> fuelCost.FuelCycleCost:
        """The fuel cycle cost at the case's prices, or with some of them changed."""
        cs = self.settingsWith(overrides)
        form = cs[econSettings.CONF_ECON_FUEL_FORM]
        firstNodes = self._firstNodes()
        if not firstNodes:
            raise ResultsError("This database has no time nodes to cost.")
        cacheKey = ("initial", form)
        if cacheKey not in self._charges:
            self._charges[cacheKey] = self.inventory(firstNodes[0]["key"], form)
        initial = self._charges[cacheKey]
        if not initial:
            raise ResultsError("There is no heavy metal in the core, so there is no fuel to cost.")
        unitCosts = fuelCost.UnitCosts.fromSettings(cs)
        lc = fuelCost.FuelCycleCost.projected(initial, unitCosts, cs)
        if economicsInterface.reloadsFromRun(cs):
            lc = lc.withReloads(self.reloadsFromRun(form, lc.cycles))
        return lc, cs

    # -- what the browser draws -------------------------------------------------------------------

    def report(self, overrides=None):
        """Everything the fuel cost view shows, as plain data."""
        lc, cs = self.cost(overrides)
        return {
            "title": self.cs.caseTitle,
            "inputs": self.editable(cs),
            **reportData(lc),
        }

    def pdf(self, path, overrides=None):
        """Write the same report as the economics plugin's PDF."""
        from armi.physics.economics import report

        lc, _cs = self.cost(overrides)
        return report.writeFuelCostReport(lc, str(path), title=self.cs.caseTitle)


def reportData(lc) -> dict:
    """A fuel cycle cost as the numbers the report shows, for drawing somewhere other than a PDF."""
    core = lc.fuelCost
    first = lc.cycleSummaries()[0]
    initial = core.byCategory()
    lifetime = lc.spendByCategory()
    perMWh = lc.lcoeByCategory()
    u = core.unitCosts
    times, paid = summary.cumulativeSpend(lc, points=300)

    def costOf(cost):
        return {
            "total": cost.total,
            "byCategory": cost.byCategory(),
            "heavyMetal": cost.heavyMetal,
            "uranium": cost.uranium,
            "assay": cost.assay,
            "swu": cost.swu,
            "feed": cost.feed,
            "oreUranium": cost.oreUranium,
            "u3o8Lb": cost.u3o8Lb,
            "tails": cost.tails,
        }

    return {
        "basis": summary.basisLine(lc),
        "source": lc.source,
        "reloadsText": summary.reloadsText(lc),
        "categories": [
            {
                "key": cat,
                "label": fuelCost.CATEGORY_LABELS[cat],
                "initial": initial.get(cat),
                "lifetime": lifetime[cat],
                "perMWh": perMWh[cat],
                "unitPrice": summary.unitPrice(cat, core),
            }
            for cat in fuelCost.COST_CATEGORIES
        ],
        "lcoe": lc.lcoe,
        "undiscountedLcoe": lc.undiscountedLcoe,
        "totalSpend": lc.totalSpend,
        "totalGeneration": lc.totalGeneration,
        "years": lc.years,
        "fullPowerYears": lc.fullPowerYears,
        "capacityFactor": lc.capacityFactor,
        "thermalPowerMW": lc.thermalPowerMW,
        "electricPowerMW": lc.electricPowerMW,
        "thermalEfficiency": lc.thermalEfficiency,
        "discountRate": lc.discountRate,
        "dischargeBurnup": lc.dischargeBurnup,
        "initialCore": costOf(core),
        "firstCycle": {
            "generation": first.generation,
            "backEnd": first.backEnd,
            "efpy": lc.cycles[0].length * lc.cycles[0].capacityFactor,
        },
        "prices": {
            "orePerLbU3O8": u.orePerLbU3O8,
            "orePerKgU": u.orePerKgU,
            "conversionPerKgU": u.conversionPerKgU,
            "conversionLoss": u.conversionLoss,
            "swu": u.swu,
            "feedAssay": u.feedAssay,
            "tailsAssay": u.tailsAssay,
            "backEndPerMWh": u.backEndPerMWh,
            "fabricationPerKgHM": u.fabricationPerKgHM,
            "leadTimes": {cat: u.leadTime(cat) for cat in fuelCost.FRONT_END},
        },
        "groups": [
            {
                "form": g.group.form,
                "assay": g.group.assay,
                "heavyMetal": g.group.heavyMetal,
                "oreUranium": g.oreUranium,
                "swu": g.requirement.swu,
                "costs": g.costs,
                "total": g.total,
            }
            for g in core.groups
        ],
        "keyPoints": summary.keyPoints(lc),
        "utilization": [list(each) for each in summary.utilization(lc)],
        "loads": [
            {"label": ch.label, "cycle": ch.cycle, "time": ch.time, **costOf(cost)}
            for ch, cost in zip(lc.charges, lc.costs)
        ],
        "cycles": [
            {
                "cycle": s.cycle,
                "start": s.start,
                "length": s.length,
                "heavyMetal": s.loaded.heavyMetal if s.loaded else None,
                "u3o8Lb": s.loaded.u3o8Lb if s.loaded else None,
                "swu": s.loaded.swu if s.loaded else None,
                "frontEnd": s.frontEnd,
                "backEnd": s.backEnd,
                "generation": s.generation,
                "costPerMWh": s.costPerMWh,
            }
            for s in lc.cycleSummaries()
        ],
        "cumulative": {"times": times, "paid": paid},
        "sensitivities": [
            {"name": name, "low": low, "high": high, "lowLabel": lowLabel, "highLabel": highLabel}
            for name, low, high, lowLabel, highLabel in lc.sensitivities()
        ],
        "waterfall": [
            {"name": name, "start": start, "end": end, "note": note} for name, start, end, note in summary.waterfall(lc)
        ],
    }
