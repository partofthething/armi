"""Interface that costs the fresh core's fuel when a run starts."""

from armi import interfaces, runLog
from armi.physics.economics import fuelCost
from armi.physics.economics import settings as econSettings
from armi.utils import tabulate

ORDER = interfaces.STACK_ORDER.POSTPROCESSING


def describeInterfaces(cs):
    """Expose the economics interface when fuel costing is on."""
    if cs[econSettings.CONF_ECON_FUEL_COST]:
        return (EconomicsInterface, {})
    return None


class EconomicsInterface(interfaces.Interface):
    """Costs the initial core's fuel, levelizes it over the plant's cycles and reports it."""

    name = "economics"

    def interactInit(self):
        super().interactInit()
        self.levelizedCost = costCore(self.r.core, self.cs)
        self.r.core.p.fuelCostInitialCore = self.levelizedCost.fuelCost.total
        self.r.core.p.fuelCostLcoe = self.levelizedCost.lcoe
        runLog.info(summarize(self.levelizedCost))
        if self.cs[econSettings.CONF_ECON_FUEL_COST_REPORT]:
            from armi.physics.economics import report

            path = report.writeFuelCostReport(
                self.levelizedCost, f"{self.cs.caseTitle}-fuel-cost.pdf", title=self.cs.caseTitle
            )
            runLog.info(f"Wrote the fuel cost report to {path}")


def costCore(core, cs) -> fuelCost.FuelLevelizedCost:
    """Cost a core's fuel at the case settings' prices and levelize it over the plant's cycles."""
    unitCosts = fuelCost.UnitCosts.fromSettings(cs)
    groups = fuelCost.fuelInventory(core, cs[econSettings.CONF_ECON_FUEL_FORM])
    return fuelCost.FuelLevelizedCost.fromSettings(fuelCost.costFuel(groups, unitCosts), cs)


def summarize(levelizedCost: fuelCost.FuelLevelizedCost) -> str:
    """Return a plain-text summary of a fuel cost."""
    cost = levelizedCost.fuelCost
    rows = [
        [
            g.group.form,
            100 * g.group.assay,
            g.group.heavyMetal,
            g.oreUranium,
            g.requirement.swu,
            *(g.costs[cat] / 1e6 for cat in fuelCost.COST_CATEGORIES),
            g.total / 1e6,
        ]
        for g in cost.groups
    ]
    categories = cost.byCategory()
    rows.append(
        [
            "Total",
            None,
            cost.heavyMetal,
            cost.oreUranium,
            cost.swu,
            *(categories[cat] / 1e6 for cat in fuelCost.COST_CATEGORIES),
            cost.total / 1e6,
        ]
    )
    headers = [
        "Form",
        "wt% U-235",
        "kgHM",
        "Nat. U kgU",
        "kg-SWU",
        "Nat. U $M",
        "Conv. $M",
        "SWU $M",
        "Fab. $M",
        "Total $M",
    ]
    cycles = levelizedCost.cycles
    return "\n".join(
        [
            "Initial core fuel cost (overnight)",
            tabulate.tabulate(rows, headers=headers, floatFmt=".2f"),
            f"Overnight cost of the initial core: ${cost.total / 1e6:,.2f}M "
            f"(${cost.total / cost.heavyMetal:,.0f}/kgHM, tails assay {100 * cost.unitCosts.tailsAssay:.3f} wt%)",
            levelizedCost.describeLcoe(),
            f"Over {len(cycles)} cycle(s), {sum(c.length for c in cycles):.2f} years at "
            f"{levelizedCost.electricPowerMW:.1f} MWe",
            f"Prices: {cost.unitCosts.describe()}",
        ]
    )
