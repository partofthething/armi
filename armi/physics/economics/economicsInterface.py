"""
Interface that costs the plant's fuel cycle.

At startup it costs the fresh core. The reloads are either projected from the initial core and reported right away,
or taken from the run: at the start of every later cycle the fresh assemblies the fuel management loaded are costed,
and the report is written at the end of the run.
"""

from armi import interfaces, runLog
from armi.physics.economics import fuelCost, summary
from armi.physics.economics import settings as econSettings
from armi.physics.fuelCycle.settings import CONF_FUEL_HANDLER_NAME
from armi.utils import tabulate

ORDER = interfaces.STACK_ORDER.POSTPROCESSING


def describeInterfaces(cs):
    """Expose the economics interface when fuel costing is on."""
    if cs[econSettings.CONF_ECON_FUEL_COST]:
        return (EconomicsInterface, {})
    return None


def reloadsFromRun(cs) -> bool:
    """Whether the reloads come from the run's fuel management rather than a projection."""
    source = cs[econSettings.CONF_ECON_RELOADS]
    if source == "auto":
        return bool(cs[CONF_FUEL_HANDLER_NAME]) and cs["nCycles"] > 1
    return source == "run"


class EconomicsInterface(interfaces.Interface):
    """Costs the initial core and every reload, levelizes them against the plant's generation and reports it."""

    name = "economics"

    def __init__(self, r, cs):
        super().__init__(r, cs)
        self.levelizedCost = None
        self._seen = set()
        self._reloads = []

    @property
    def fromRun(self) -> bool:
        return reloadsFromRun(self.cs)

    def interactInit(self):
        super().interactInit()
        self.levelizedCost = costCore(self.r.core, self.cs)
        self._seen = {a.getName() for a in self.r.core}
        if self.fromRun:
            runLog.info(
                f"Initial core fuel: ${self.levelizedCost.fuelCost.total / 1e6:,.2f}M. Reloads will be costed as the "
                "fuel management loads them; the full fuel cycle cost comes at the end of the run."
            )
        self._publish()

    def interactBOC(self, cycle=None):
        """Cost the fresh assemblies the fuel management loaded for this cycle."""
        if not self.fromRun:
            return
        new = [a for a in self.r.core if a.getName() not in self._seen]
        self._seen.update(a.getName() for a in new)
        fresh = [a for a in new if fuelCost.isFresh(a)]
        if not cycle or not fresh:
            return
        groups = fuelCost.fuelInventory(self.r.core, self.cs[econSettings.CONF_ECON_FUEL_FORM], fresh)
        start = self.levelizedCost.cycles[cycle].start
        self._reloads.append(fuelCost.FuelCharge(start, cycle, groups, f"Reload {cycle}"))
        heavyMetal = sum(g.heavyMetal for g in groups)
        runLog.info(f"Cycle {cycle} loads {len(fresh)} fresh assemblies, {heavyMetal:,.0f} kgHM.")

    def interactEOL(self):
        if self.fromRun:
            self.levelizedCost = self.levelizedCost.withReloads(self._reloads)
            self._publish()

    def _publish(self):
        """Put the cost on the reactor, log it and write the report."""
        lc = self.levelizedCost
        self.r.core.p.fuelCostInitialCore = lc.fuelCost.total
        self.r.core.p.fuelCostLcoe = lc.lcoe
        if self.fromRun and lc.source != "run":
            return
        runLog.info(summarize(lc))
        if self.cs[econSettings.CONF_ECON_FUEL_COST_REPORT]:
            from armi.physics.economics import report

            path = report.writeFuelCostReport(lc, f"{self.cs.caseTitle}-fuel-cost.pdf", title=self.cs.caseTitle)
            runLog.info(f"Wrote the fuel cost report to {path}")


def costCore(core, cs) -> fuelCost.FuelCycleCost:
    """Cost a core's fuel at the case settings' prices, with projected reloads, over the plant's cycles."""
    unitCosts = fuelCost.UnitCosts.fromSettings(cs)
    groups = fuelCost.fuelInventory(core, cs[econSettings.CONF_ECON_FUEL_FORM])
    return fuelCost.FuelCycleCost.projected(groups, unitCosts, cs)


def summarize(lc: fuelCost.FuelCycleCost) -> str:
    """Return a plain-text summary of a fuel cycle cost."""
    core = lc.fuelCost
    quantities = (
        f"Initial core: {core.heavyMetal / 1e3:,.2f} tHM of uranium at {100 * core.assay:.2f} wt% average needs "
        f"{core.oreUranium / 1e3:,.1f} tU natural uranium ({core.u3o8Lb / 1e6:,.3f} Mlb U3O8), "
        f"{core.feed / 1e3:,.1f} tU conversion and {core.swu / 1e3:,.1f} tSWU; "
        f"{core.tails / 1e3:,.1f} tU tails at {100 * core.unitCosts.tailsAssay:.3f} wt%"
    )
    groupRows = [
        [
            g.group.form,
            100 * g.group.assay,
            g.group.heavyMetal,
            g.oreUranium,
            g.requirement.swu,
            *(g.costs[cat] / 1e6 for cat in fuelCost.FRONT_END),
            g.total / 1e6,
        ]
        for g in core.groups
    ]
    initial = core.byCategory()
    groupRows.append(
        [
            "Total",
            None,
            core.heavyMetal,
            core.oreUranium,
            core.swu,
            *(initial[cat] / 1e6 for cat in fuelCost.FRONT_END),
            core.total / 1e6,
        ]
    )
    groupHeaders = ["Form", "wt% U-235", "kgHM", "Nat. U kgU", "kg-SWU", "Nat. U $M", "Conv. $M", "SWU $M"]
    groupHeaders += ["Fab. $M", "Total $M"]

    lifetime = lc.spendByCategory()
    levelized = lc.lcoeByCategory()
    stepRows = [
        [fuelCost.CATEGORY_LABELS[cat], initial.get(cat, 0.0) / 1e6, lifetime[cat] / 1e6, levelized[cat]]
        for cat in fuelCost.COST_CATEGORIES
    ]
    stepRows.append(["Total", core.total / 1e6, lc.totalSpend / 1e6, lc.lcoe])

    cycleRows = [
        [
            s.cycle + 1,
            s.start,
            s.loaded.heavyMetal if s.loaded else 0.0,
            s.loaded.swu if s.loaded else 0.0,
            s.frontEnd / 1e6,
            s.generation / 1e3,
            s.costPerMWh,
        ]
        for s in lc.cycleSummaries()
    ]
    return "\n".join(
        [
            "Fuel cycle cost",
            quantities,
            tabulate.tabulate(groupRows, headers=groupHeaders, floatFmt=".2f"),
            tabulate.tabulate(
                stepRows,
                headers=["Step", "Initial core $M", "Lifetime $M", "Levelized $/MWh"],
                floatFmt=".2f",
            ),
            f"Cycle length: {summary.cycleLengthText(lc)}",
            f"Reloads: {describeReloads(lc)}",
            tabulate.tabulate(
                cycleRows,
                headers=["Cycle", "Start y", "Loaded kgHM", "kg-SWU", "Fuel $M", "GWh", "$/MWh"],
                floatFmt=".2f",
            ),
            lc.describeLcoe(),
            f"Over {len(lc.cycles)} cycle(s), {lc.years:.2f} years at {lc.electricPowerMW:.1f} MWe, "
            f"{lc.totalGeneration / 1e6:,.2f} TWh; discharge burnup {lc.dischargeBurnup:,.1f} GWd/tHM (life average), "
            f"{sum(c.oreUranium for c in lc.costs) / (lc.totalGeneration / 1e3):,.2f} kgU natural and "
            f"{sum(c.swu for c in lc.costs) / (lc.totalGeneration / 1e3):,.2f} kg-SWU per GWh(e)",
            f"Prices: {lc.unitCosts.describe()}",
        ]
    )


def describeReloads(lc: fuelCost.FuelCycleCost) -> str:
    """Say where the reloads came from."""
    if lc.source == "run":
        return f"{len(lc.reloads)} batches of fresh fuel loaded by the run's fuel management"
    if not lc.reloads:
        return "none"
    return f"projected, {lc.reloadFraction:.0%} of the initial core at the start of every later cycle"
