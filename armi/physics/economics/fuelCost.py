"""
Fuel cycle cost of a reactor: uranium ore, conversion, enrichment, fabrication and spent fuel.

Fuel is grouped by fuel form and enrichment; each group's uranium is costed back to ore through
:py:mod:`~armi.physics.economics.enrichment`, and all of its heavy metal pays the form's fabrication cost. Masses
come from the reactor model, scaled to the full core when the model is a symmetric fraction of it.

A plant buys fuel in charges: the initial core at first startup, then a reload at the start of each later cycle.
Each front-end step is paid its lead time before the fuel it makes is loaded, and spent fuel pays a fee on every MWh
generated. :py:class:`FuelCycleCost` levelizes all of it against the plant's generation.

Heavy metal other than uranium (plutonium, thorium) pays fabrication but no material cost here; the run log says
how much of it there is.
"""

from dataclasses import dataclass, field, replace
from typing import Iterable, Optional

from armi import runLog
from armi.physics.economics import enrichment, levelized
from armi.physics.economics import settings as econSettings

#: Oxygen over heavy metal, by mass, above which a fuel is an oxide (UO2 is 0.134; carbides and metals are 0).
OXIDE_O_TO_HM = 0.05

#: Enrichments that agree to this many decimal places share a fuel group.
ASSAY_DECIMALS = 5

#: Front-end steps, paid per charge of fresh fuel.
FRONT_END = ("ore", "conversion", "enrichment", "fabrication")

#: Every step of the fuel cycle that is costed: the front end, then spent fuel, paid per MWh generated.
COST_CATEGORIES = (*FRONT_END, "backEnd")

CATEGORY_LABELS = {
    "ore": "Natural uranium (U3O8)",
    "conversion": "Conversion to UF6",
    "enrichment": "Enrichment (SWU)",
    "fabrication": "Fabrication",
    "backEnd": "Spent fuel (back end)",
}

#: Burnup, percent FIMA, below which an assembly counts as fresh fuel.
FRESH_BURNUP = 1e-6


@dataclass(frozen=True)
class UnitCosts:
    """Prices, lead times and enrichment plant assumptions, in dollars and years."""

    orePerLbU3O8: float
    conversionPerKgU: float
    conversionLoss: float
    swu: float
    fabricationPerKgHM: dict[str, float]
    feedAssay: float = enrichment.NATURAL_ASSAY
    tailsAssay: float = enrichment.DEFAULT_TAILS_ASSAY
    backEndPerMWh: float = 0.0
    leadTimes: dict[str, float] = field(default_factory=dict)
    """Years before the fuel is loaded that each front-end step is paid, by category."""

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
            backEndPerMWh=cs[econSettings.CONF_ECON_BACK_END_COST],
            leadTimes={cat: cs[name] for cat, name in econSettings.LEAD_TIMES.items()},
        )
        if cs[econSettings.CONF_ECON_OPTIMIZE_TAILS]:
            tails = enrichment.optimumTailsAssay(unitCosts.feedPerKgU, unitCosts.swu, unitCosts.feedAssay)
            unitCosts = replace(unitCosts, tailsAssay=tails)
        return unitCosts

    @property
    def orePerKgU(self) -> float:
        """Natural uranium as U3O8, per kg of uranium."""
        return enrichment.feedCostPerKgU(self.orePerLbU3O8)

    @property
    def feedPerKgU(self) -> float:
        """Natural uranium delivered to the enrichment plant as UF6 (ore with conversion losses, and conversion)."""
        return enrichment.feedCostPerKgU(self.orePerLbU3O8, self.conversionPerKgU, self.conversionLoss)

    def leadTime(self, category: str) -> float:
        """Years before loading that a category is paid (spent fuel is paid as the electricity is made)."""
        return self.leadTimes.get(category, 0.0)

    def scaled(self, category: str, factor: float) -> "UnitCosts":
        """These prices with one category's price multiplied by ``factor``."""
        if category == "ore":
            return replace(self, orePerLbU3O8=self.orePerLbU3O8 * factor)
        if category == "conversion":
            return replace(self, conversionPerKgU=self.conversionPerKgU * factor)
        if category == "enrichment":
            return replace(self, swu=self.swu * factor)
        if category == "fabrication":
            return replace(self, fabricationPerKgHM={k: v * factor for k, v in self.fabricationPerKgHM.items()})
        if category == "backEnd":
            return replace(self, backEndPerMWh=self.backEndPerMWh * factor)
        raise ValueError(f"Unknown cost category {category}")

    def describe(self) -> str:
        """Return the prices and assumptions as text."""
        fabrication = ", ".join(f"{form} ${price:,.0f}/kgHM" for form, price in self.fabricationPerKgHM.items())
        leads = ", ".join(f"{CATEGORY_LABELS[cat]} {self.leadTime(cat):g} y" for cat in FRONT_END)
        return (
            f"Natural uranium ${self.orePerLbU3O8:.0f}/lb U3O8 (${self.orePerKgU:.0f}/kgU), "
            f"conversion ${self.conversionPerKgU:.0f}/kgU ({100 * self.conversionLoss:.1f}% loss)\n"
            f"Enrichment ${self.swu:.0f}/SWU, "
            f"feed {100 * self.feedAssay:.3f} wt%, tails {100 * self.tailsAssay:.3f} wt%\n"
            f"Fabrication: {fabrication}\n"
            f"Spent fuel ${self.backEndPerMWh:.2f}/MWh\n"
            f"Paid before loading: {leads}"
        )


@dataclass
class FuelGroup:
    """All of the fuel of one form at one enrichment in a charge."""

    form: str
    heavyMetal: float = 0.0
    """Heavy metal, kg."""

    uranium: float = 0.0
    """kg of uranium."""

    u235: float = 0.0
    """kg of U-235."""

    @property
    def assay(self) -> float:
        """U-235 mass fraction of the uranium."""
        return self.u235 / self.uranium if self.uranium else 0.0

    def scaled(self, factor: float) -> "FuelGroup":
        return FuelGroup(self.form, self.heavyMetal * factor, self.uranium * factor, self.u235 * factor)


@dataclass(frozen=True)
class FuelGroupCost:
    """What one fuel group takes to make, and what it costs."""

    group: FuelGroup
    requirement: enrichment.EnrichmentRequirement
    oreUranium: float
    """kg of uranium bought as U3O8, conversion losses included."""

    costs: dict[str, float] = field(default_factory=dict)
    """Dollars by front-end category (see ``FRONT_END``)."""

    @property
    def total(self) -> float:
        return sum(self.costs.values())


@dataclass(frozen=True)
class FuelCost:
    """The quantities and cost of one charge of fresh fuel."""

    groups: list[FuelGroupCost]
    unitCosts: UnitCosts

    @property
    def total(self) -> float:
        return sum(g.total for g in self.groups)

    @property
    def heavyMetal(self) -> float:
        """Heavy metal, kg."""
        return sum(g.group.heavyMetal for g in self.groups)

    @property
    def uranium(self) -> float:
        """Enriched uranium, kg."""
        return sum(g.group.uranium for g in self.groups)

    @property
    def assay(self) -> float:
        """Average U-235 mass fraction of the uranium."""
        return sum(g.group.u235 for g in self.groups) / self.uranium if self.uranium else 0.0

    @property
    def swu(self) -> float:
        """kg-SWU of separative work."""
        return sum(g.requirement.swu for g in self.groups)

    @property
    def feed(self) -> float:
        """Natural uranium fed to enrichment as UF6 (what conversion delivers), kg."""
        return sum(g.requirement.feed for g in self.groups)

    @property
    def oreUranium(self) -> float:
        """Natural uranium bought as U3O8, conversion losses included, kg."""
        return sum(g.oreUranium for g in self.groups)

    @property
    def u3o8Lb(self) -> float:
        """Pounds of U3O8 bought."""
        return self.oreUranium * enrichment.U3O8_PER_U * enrichment.LB_PER_KG

    @property
    def tails(self) -> float:
        """Depleted uranium left at the enrichment plant, kg."""
        return sum(g.requirement.tails for g in self.groups)

    def byCategory(self) -> dict[str, float]:
        """Dollars by front-end category."""
        return {cat: sum(g.costs[cat] for g in self.groups) for cat in FRONT_END}


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


def fuelInventory(core, form: str = "auto", assemblies: Optional[Iterable] = None) -> list[FuelGroup]:
    """
    Return heavy metal grouped by fuel form and uranium enrichment, in kg, scaled to the full core.

    Parameters
    ----------
    core : Core
        The core to take stock of.
    form : str
        A fuel form for all of the fuel, or ``"auto"`` to classify each component (:py:func:`classifyFuelForm`).
    assemblies : iterable of Assembly, optional
        Only these assemblies (e.g. a reload batch) instead of the whole core.
    """
    kgPerGram = core.powerMultiplier / 1000.0
    blocks = core.iterBlocks() if assemblies is None else (b for a in assemblies for b in a)
    groups: dict[tuple[str, float], FuelGroup] = {}
    for component in _heavyMetalComponents(blocks):
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


def isFresh(assembly) -> bool:
    """Whether an assembly holds heavy metal that has never been irradiated."""
    fuelBlocks = [b for b in assembly if b.getHMMass() > 0.0]
    return bool(fuelBlocks) and all((b.p.percentBu or 0.0) < FRESH_BURNUP for b in fuelBlocks)


def costFuel(groups: Iterable[FuelGroup], unitCosts: UnitCosts) -> FuelCost:
    """Return what it takes, and costs, to make the fuel groups."""
    return FuelCost([_costGroup(group, unitCosts) for group in groups], unitCosts)


def _costGroup(group: FuelGroup, unitCosts: UnitCosts) -> FuelGroupCost:
    req = enrichment.enrich(group.uranium, group.assay, unitCosts.feedAssay, unitCosts.tailsAssay)
    oreUranium = req.feed / (1.0 - unitCosts.conversionLoss)
    costs = {
        "ore": oreUranium * unitCosts.orePerKgU,
        "conversion": req.feed * unitCosts.conversionPerKgU,
        "enrichment": req.swu * unitCosts.swu,
        "fabrication": group.heavyMetal * unitCosts.fabricationPerKgHM[group.form],
    }
    return FuelGroupCost(group, req, oreUranium, costs)


def _heavyMetalComponents(blocks):
    for block in blocks:
        for component in block.iterComponents():
            if component.getHMMass() > 0.0:
                yield component


@dataclass(frozen=True)
class FuelCharge:
    """A batch of fresh fuel loaded into the core."""

    time: float
    """Years from first startup when the fuel is loaded."""

    cycle: int
    """The cycle the fuel is loaded for (0 is the initial core)."""

    groups: list[FuelGroup]

    label: str = ""


@dataclass(frozen=True)
class CycleSummary:
    """The fresh fuel loaded for one cycle and the electricity the cycle makes."""

    cycle: int
    start: float
    length: float
    loaded: Optional[FuelCost]
    """The fresh fuel loaded at the start of the cycle (None if none was)."""

    backEnd: float
    """Dollars of spent fuel fees on the cycle's generation."""

    generation: float
    """Net MWh."""

    @property
    def frontEnd(self) -> float:
        """Dollars of fresh fuel loaded for the cycle."""
        return self.loaded.total if self.loaded else 0.0

    @property
    def total(self) -> float:
        return self.frontEnd + self.backEnd

    @property
    def costPerMWh(self) -> float:
        """The fuel bought for the cycle over the cycle's own electricity, undiscounted."""
        return self.total / self.generation if self.generation else 0.0


@dataclass(frozen=True)
class FuelCycleCost:
    """
    A plant's fuel cycle cost: the charges of fresh fuel, spent fuel, and the electricity they make.

    ``charges[0]`` is the initial core. ``source`` says where the reloads came from: ``"run"`` for the fresh fuel the
    ARMI run's fuel management actually loaded, ``"projected"`` for ``reloadFraction`` of the initial core at the start
    of every later cycle.
    """

    charges: list[FuelCharge]
    unitCosts: UnitCosts
    cycles: list[levelized.Cycle]
    thermalPowerMW: float
    thermalEfficiency: float
    discountRate: float
    source: str = "projected"
    reloadFraction: Optional[float] = None

    @classmethod
    def projected(cls, initialCore: list[FuelGroup], unitCosts: UnitCosts, cs) -> "FuelCycleCost":
        """The initial core, then ``econReloadFraction`` of it again at the start of every later cycle."""
        cycles = levelized.cyclesFromSettings(cs)
        fraction = cs[econSettings.CONF_ECON_RELOAD_FRACTION]
        charges = [FuelCharge(0.0, 0, initialCore, "Initial core")]
        if fraction > 0.0:
            charges += [
                FuelCharge(c.start, i, [g.scaled(fraction) for g in initialCore], f"Reload {i}")
                for i, c in enumerate(cycles)
                if i > 0
            ]
        return cls(
            charges,
            unitCosts,
            cycles,
            cs["power"] / 1e6,
            cs[econSettings.CONF_ECON_THERMAL_EFFICIENCY],
            cs[econSettings.CONF_ECON_DISCOUNT_RATE],
            "projected",
            fraction,
        )

    def withReloads(self, reloads: list[FuelCharge]) -> "FuelCycleCost":
        """The same cost with the reloads the run actually loaded in place of the projected ones."""
        return replace(self, charges=[self.charges[0], *reloads], source="run", reloadFraction=None)

    def repriced(self, unitCosts: UnitCosts) -> "FuelCycleCost":
        return replace(self, unitCosts=unitCosts)

    def withCapacityFactor(self, capacityFactor: float) -> "FuelCycleCost":
        """The same fuel at another capacity factor: each cycle makes the same energy over a different calendar time."""
        cycles, start = [], 0.0
        for c in self.cycles:
            length = c.length * c.capacityFactor / capacityFactor
            cycles.append(levelized.Cycle(start, length, capacityFactor))
            start += length
        charges = [replace(ch, time=cycles[ch.cycle].start) for ch in self.charges]
        return replace(self, cycles=cycles, charges=charges)

    # --- quantities ---

    @property
    def costs(self) -> list[FuelCost]:
        """What each charge takes and costs, in ``charges`` order."""
        return [costFuel(ch.groups, self.unitCosts) for ch in self.charges]

    @property
    def fuelCost(self) -> FuelCost:
        """The initial core."""
        return costFuel(self.charges[0].groups, self.unitCosts)

    @property
    def reloads(self) -> list[FuelCharge]:
        return self.charges[1:]

    @property
    def electricPowerMW(self) -> float:
        """Net electric power at full power."""
        return self.thermalPowerMW * self.thermalEfficiency

    @property
    def years(self) -> float:
        """Calendar years over all of the cycles."""
        return sum(c.length for c in self.cycles)

    @property
    def capacityFactor(self) -> float:
        """Energy made over the energy at full power for the whole time, over all of the cycles."""
        return sum(c.length * c.capacityFactor for c in self.cycles) / self.years

    @property
    def fullPowerYears(self) -> float:
        """Effective full power years over all of the cycles."""
        return self.years * self.capacityFactor

    @property
    def generation(self) -> list[tuple[float, float]]:
        """Net generation, as ``(year, MWh)``."""
        return levelized.generationByCycle(self.cycles, self.electricPowerMW)

    @property
    def thermalEnergyMWd(self) -> float:
        """Thermal energy over all of the cycles, MWd."""
        return sum(self.thermalPowerMW * c.capacityFactor * c.length * levelized.DAYS_PER_YEAR for c in self.cycles)

    @property
    def dischargeBurnup(self) -> float:
        """
        Thermal energy over all of the heavy metal loaded, MWd/kgHM.

        The plant-life average discharge burnup, if every load is burned out by the end of the last cycle.
        """
        return self.thermalEnergyMWd / sum(c.heavyMetal for c in self.costs)

    @property
    def totalGeneration(self) -> float:
        """Net MWh over all of the cycles."""
        return sum(mwh for _, mwh in self.generation)

    # --- cash flows ---

    def cashFlows(self, category: str) -> list[tuple[float, float]]:
        """``(year, dollars)`` paid for one cost category over the plant's life."""
        if category == "backEnd":
            return [(year, mwh * self.unitCosts.backEndPerMWh) for year, mwh in self.generation]
        lead = self.unitCosts.leadTime(category)
        return [(ch.time - lead, cost.byCategory()[category]) for ch, cost in zip(self.charges, self.costs)]

    @property
    def purchases(self) -> list[tuple[float, float]]:
        """Every payment, as ``(year, dollars)``."""
        return [flow for cat in COST_CATEGORIES for flow in self.cashFlows(cat)]

    def spendByCategory(self) -> dict[str, float]:
        """Undiscounted dollars over the plant's life, by category."""
        return {cat: sum(amount for _, amount in self.cashFlows(cat)) for cat in COST_CATEGORIES}

    @property
    def totalSpend(self) -> float:
        """Undiscounted dollars over the plant's life."""
        return sum(self.spendByCategory().values())

    def lcoeByCategory(self, discounted: bool = True) -> dict[str, float]:
        """Levelized fuel cycle cost, $/MWh, by category."""
        rate = self.discountRate if discounted else 0.0
        energy = levelized.presentValue(self.generation, rate)
        return {cat: levelized.presentValue(self.cashFlows(cat), rate) / energy for cat in COST_CATEGORIES}

    @property
    def lcoe(self) -> float:
        """Levelized fuel cycle cost of electricity, $/MWh."""
        return sum(self.lcoeByCategory().values())

    @property
    def undiscountedLcoe(self) -> float:
        """Everything paid for fuel over all the electricity made, $/MWh, with no discounting."""
        return sum(self.lcoeByCategory(discounted=False).values())

    def cycleSummaries(self) -> list[CycleSummary]:
        """The fresh fuel loaded for each cycle against the electricity the cycle makes."""
        loaded = {ch.cycle: cost for ch, cost in zip(self.charges, self.costs)}
        return [
            CycleSummary(i, c.start, c.length, loaded.get(i), mwh * self.unitCosts.backEndPerMWh, mwh)
            for i, (c, (_, mwh)) in enumerate(zip(self.cycles, self.generation))
        ]

    def describeLcoe(self) -> str:
        """Return the levelized fuel cycle cost and its split by category as text."""
        parts = " + ".join(f"{CATEGORY_LABELS[cat]} ${value:.2f}" for cat, value in self.lcoeByCategory().items())
        return (
            f"Levelized fuel cycle cost: ${self.lcoe:.2f}/MWh at a {100 * self.discountRate:.1f}% discount rate "
            f"(${self.undiscountedLcoe:.2f}/MWh undiscounted)\n  = {parts}"
        )

    def sensitivities(self, priceFactor: float = 0.5) -> list[tuple[str, float, float, str, str]]:
        """
        Levelized cost at a low and a high value of each input, as ``(input, low $/MWh, high $/MWh, low, high)``.

        Prices move by ``priceFactor`` either way; the discount rate and capacity factor move to typical bounds. The
        rows come biggest swing first.
        """
        rows = []
        for cat in COST_CATEGORIES:
            low = self.repriced(self.unitCosts.scaled(cat, 1.0 - priceFactor)).lcoe
            high = self.repriced(self.unitCosts.scaled(cat, 1.0 + priceFactor)).lcoe
            rows.append((CATEGORY_LABELS[cat], low, high, f"-{priceFactor:.0%}", f"+{priceFactor:.0%}"))
        rateLow, rateHigh = max(self.discountRate - 0.04, 0.0), self.discountRate + 0.03
        rows.append(
            (
                "Discount rate",
                replace(self, discountRate=rateLow).lcoe,
                replace(self, discountRate=rateHigh).lcoe,
                f"{rateLow:.0%}",
                f"{rateHigh:.0%}",
            )
        )
        cf = self.capacityFactor
        cfLow, cfHigh = max(cf - 0.15, 0.05), min(cf + 0.05, 1.0)
        rows.append(
            (
                "Capacity factor",
                self.withCapacityFactor(cfLow).lcoe,
                self.withCapacityFactor(cfHigh).lcoe,
                f"{cfLow:.0%}",
                f"{cfHigh:.0%}",
            )
        )
        return sorted(rows, key=lambda r: -abs(r[2] - r[1]))
