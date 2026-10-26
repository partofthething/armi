"""
Front-end fuel cycle cost of a core: uranium ore, conversion, enrichment and fabrication.

The core's fuel is grouped by fuel form and enrichment; each group's uranium is costed back to ore through
:py:mod:`~armi.physics.economics.enrichment`, and all of its heavy metal pays the form's fabrication cost. Masses
come from the reactor model, scaled to the full core when the model is a symmetric fraction of it.

Heavy metal other than uranium (plutonium, thorium) pays fabrication but no material cost here; the run log says
how much of it there is.
"""

from dataclasses import dataclass, field, replace
from typing import Iterable

from armi import runLog
from armi.physics.economics import enrichment, levelized
from armi.physics.economics import settings as econSettings

#: Oxygen over heavy metal, by mass, above which a fuel is an oxide (UO2 is 0.134; carbides and metals are 0).
OXIDE_O_TO_HM = 0.05

#: Enrichments that agree to this many decimal places share a fuel group.
ASSAY_DECIMALS = 5

COST_CATEGORIES = ("ore", "conversion", "enrichment", "fabrication")

CATEGORY_LABELS = {
    "ore": "Natural uranium (U3O8)",
    "conversion": "Conversion",
    "enrichment": "Enrichment (SWU)",
    "fabrication": "Fabrication",
}


@dataclass(frozen=True)
class UnitCosts:
    """Prices and enrichment plant assumptions, in dollars."""

    orePerLbU3O8: float
    conversionPerKgU: float
    conversionLoss: float
    swu: float
    fabricationPerKgHM: dict[str, float]
    feedAssay: float = enrichment.NATURAL_ASSAY
    tailsAssay: float = enrichment.DEFAULT_TAILS_ASSAY

    @classmethod
    def fromSettings(cls, cs) -> "UnitCosts":
        unitCosts = cls(
            orePerLbU3O8=cs[econSettings.CONF_ECON_ORE_PRICE],
            conversionPerKgU=cs[econSettings.CONF_ECON_CONVERSION_COST],
            conversionLoss=cs[econSettings.CONF_ECON_CONVERSION_LOSS],
            swu=cs[econSettings.CONF_ECON_SWU_COST],
            fabricationPerKgHM={form: cs[name] for form, name in econSettings.FUEL_FORMS.items()},
            feedAssay=cs[econSettings.CONF_ECON_FEED_ASSAY],
            tailsAssay=cs[econSettings.CONF_ECON_TAILS_ASSAY],
        )
        if cs[econSettings.CONF_ECON_OPTIMIZE_TAILS]:
            tails = enrichment.optimumTailsAssay(unitCosts.feedPerKgU, unitCosts.swu, unitCosts.feedAssay)
            unitCosts = replace(unitCosts, tailsAssay=tails)
        return unitCosts

    @property
    def orePerKgU(self) -> float:
        """Natural uranium as U3O8, per kg of uranium."""
        return enrichment.feedCostPerKgU(self.orePerLbU3O8)

    def describe(self) -> str:
        """Return the prices and assays as text."""
        fabrication = ", ".join(f"{form} ${price:,.0f}/kgHM" for form, price in self.fabricationPerKgHM.items())
        return (
            f"Natural uranium ${self.orePerLbU3O8:.0f}/lb U3O8 (${self.orePerKgU:.0f}/kgU), "
            f"conversion ${self.conversionPerKgU:.0f}/kgU ({100 * self.conversionLoss:.1f}% loss)\n"
            f"Enrichment ${self.swu:.0f}/SWU, "
            f"feed {100 * self.feedAssay:.3f} wt%, tails {100 * self.tailsAssay:.3f} wt%\n"
            f"Fabrication: {fabrication}"
        )

    @property
    def feedPerKgU(self) -> float:
        """Natural uranium delivered to the enrichment plant as UF6 (ore with conversion losses, and conversion)."""
        return enrichment.feedCostPerKgU(self.orePerLbU3O8, self.conversionPerKgU, self.conversionLoss)


@dataclass
class FuelGroup:
    """All of the core's fuel of one form at one enrichment."""

    form: str
    heavyMetal: float = 0.0
    """kg of heavy metal."""

    uranium: float = 0.0
    """kg of uranium."""

    u235: float = 0.0
    """kg of U-235."""

    @property
    def assay(self) -> float:
        """U-235 mass fraction of the uranium."""
        return self.u235 / self.uranium if self.uranium else 0.0


@dataclass(frozen=True)
class FuelGroupCost:
    """What one fuel group takes to make, and what it costs."""

    group: FuelGroup
    requirement: enrichment.EnrichmentRequirement
    oreUranium: float
    """kg of uranium bought as U3O8, conversion losses included."""

    costs: dict[str, float] = field(default_factory=dict)
    """Dollars by category (see ``COST_CATEGORIES``)."""

    @property
    def total(self) -> float:
        return sum(self.costs.values())


@dataclass(frozen=True)
class FuelCost:
    """The cost of a core's worth of fresh fuel."""

    groups: list[FuelGroupCost]
    unitCosts: UnitCosts

    @property
    def total(self) -> float:
        return sum(g.total for g in self.groups)

    @property
    def heavyMetal(self) -> float:
        return sum(g.group.heavyMetal for g in self.groups)

    @property
    def swu(self) -> float:
        return sum(g.requirement.swu for g in self.groups)

    @property
    def oreUranium(self) -> float:
        return sum(g.oreUranium for g in self.groups)

    def byCategory(self) -> dict[str, float]:
        return {cat: sum(g.costs[cat] for g in self.groups) for cat in COST_CATEGORIES}


def classifyFuelForm(component) -> str:
    """
    Guess the fuel form of a heavy-metal component from what it and its block are made of.

    TRISO if the block holds silicon and at least as much carbon as heavy metal (silicon carbide coatings in a graphite
    matrix; the trace carbon of steels or a B4C absorber is far less), UO2 if the component is an oxide, U-metal
    otherwise.
    """
    block = component.parent
    if block is not None and block.getMass("SI") > 0.0 and block.getMass("C") >= block.getHMMass():
        return "TRISO"
    if component.getMass("O") > OXIDE_O_TO_HM * component.getHMMass():
        return "UO2"
    return "U-metal"


def fuelInventory(core, form: str = "auto") -> list[FuelGroup]:
    """
    Return the core's heavy metal grouped by fuel form and uranium enrichment, in kg, for the full core.

    Parameters
    ----------
    core : Core
        The core to take stock of.
    form : str
        A fuel form for all of the fuel, or ``"auto"`` to classify each component (:py:func:`classifyFuelForm`).
    """
    kgPerGram = core.powerMultiplier / 1000.0
    groups: dict[tuple[str, float], FuelGroup] = {}
    for component in _heavyMetalComponents(core):
        uranium = component.getMass("U")
        u235 = component.getMass("U235")
        componentForm = classifyFuelForm(component) if form == "auto" else form
        assay = round(u235 / uranium, ASSAY_DECIMALS) if uranium else 0.0
        group = groups.setdefault((componentForm, assay), FuelGroup(componentForm))
        group.heavyMetal += component.getHMMass() * kgPerGram
        group.uranium += uranium * kgPerGram
        group.u235 += u235 * kgPerGram

    nonUranium = sum(g.heavyMetal - g.uranium for g in groups.values())
    if nonUranium > 1e-3:
        runLog.warning(f"{nonUranium:.1f} kg of non-uranium heavy metal is costed for fabrication only.")
    return [groups[key] for key in sorted(groups)]


def costFuel(groups: Iterable[FuelGroup], unitCosts: UnitCosts) -> FuelCost:
    """Return what it costs to make the fuel groups."""
    return FuelCost([_costGroup(group, unitCosts) for group in groups], unitCosts)


def _costGroup(group: FuelGroup, unitCosts: UnitCosts) -> FuelGroupCost:
    req = enrichment.enrich(group.uranium, group.assay, unitCosts.feedAssay, unitCosts.tailsAssay)
    oreUranium = req.feed / (1.0 - unitCosts.conversionLoss)
    costs = {
        "ore": oreUranium * enrichment.feedCostPerKgU(unitCosts.orePerLbU3O8),
        "conversion": req.feed * unitCosts.conversionPerKgU,
        "enrichment": req.swu * unitCosts.swu,
        "fabrication": group.heavyMetal * unitCosts.fabricationPerKgHM[group.form],
    }
    return FuelGroupCost(group, req, oreUranium, costs)


def _heavyMetalComponents(core):
    for block in core.iterBlocks():
        for component in block.iterComponents():
            if component.getHMMass() > 0.0:
                yield component


@dataclass(frozen=True)
class FuelLevelizedCost:
    """
    A core's fuel cost levelized over the plant's cycles.

    The initial core is bought at first startup, and ``reloadFraction`` of it again at the start of every later cycle.
    """

    fuelCost: FuelCost
    cycles: list[levelized.Cycle]
    electricPowerMW: float
    discountRate: float
    reloadFraction: float = 1.0

    @classmethod
    def fromSettings(cls, fuelCost: FuelCost, cs) -> "FuelLevelizedCost":
        return cls(
            fuelCost,
            levelized.cyclesFromSettings(cs),
            cs["power"] / 1e6 * cs[econSettings.CONF_ECON_THERMAL_EFFICIENCY],
            cs[econSettings.CONF_ECON_DISCOUNT_RATE],
            cs[econSettings.CONF_ECON_RELOAD_FRACTION],
        )

    @property
    def purchases(self) -> list[tuple[float, float]]:
        """Fuel bought, as ``(year, dollars)``."""
        return [
            (cycle.start, self.fuelCost.total * (1.0 if i == 0 else self.reloadFraction))
            for i, cycle in enumerate(self.cycles)
        ]

    @property
    def generation(self) -> list[tuple[float, float]]:
        """Net generation, as ``(year, MWh)``."""
        return levelized.generationByCycle(self.cycles, self.electricPowerMW)

    @property
    def lcoe(self) -> float:
        """Levelized fuel cost of electricity, $/MWh."""
        return levelized.levelizedCost(self.purchases, self.generation, self.discountRate)

    @property
    def undiscountedLcoe(self) -> float:
        """Fuel bought over electricity made, $/MWh, with no discounting."""
        return levelized.levelizedCost(self.purchases, self.generation, 0.0)

    def lcoeByCategory(self, discounted: bool = True) -> dict[str, float]:
        """Levelized fuel cost, $/MWh, split by cost category (every reload costs in the same proportions)."""
        lcoe = self.lcoe if discounted else self.undiscountedLcoe
        return {cat: lcoe * cost / self.fuelCost.total for cat, cost in self.fuelCost.byCategory().items()}

    def describeLcoe(self) -> str:
        """Return the levelized fuel cost and its split by category as text."""
        parts = " + ".join(f"{CATEGORY_LABELS[cat]} ${value:.2f}" for cat, value in self.lcoeByCategory().items())
        return (
            f"Levelized fuel cost: ${self.lcoe:.2f}/MWh at a {100 * self.discountRate:.1f}% discount rate "
            f"(${self.undiscountedLcoe:.2f}/MWh undiscounted)\n  = {parts}"
        )
