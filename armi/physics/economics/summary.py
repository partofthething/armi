"""
The numbers behind the fuel cost report, worked out once for every way it is shown.

The PDF (:py:mod:`~armi.physics.economics.report`) and the browser's fuel cost view draw the same figures. Everything
derived from a :py:class:`~armi.physics.economics.fuelCost.FuelCycleCost` beyond its own properties is computed here,
as plain numbers and strings, so the two can never disagree. Nothing here draws, so the browser view does not pay for
importing matplotlib.
"""

import math
from dataclasses import replace

from armi.physics.economics import fuelCost, levelized


def money(dollars: float) -> str:
    """Dollars as millions, or billions past a thousand million."""
    return f"${dollars / 1e9:,.2f}B" if dollars >= 1e9 else f"${dollars / 1e6:,.1f}M"


def keyPoints(lc) -> list[str]:
    """Three or four computed sentences a decision maker should take away."""
    shares = lc.lcoeByCategory()
    biggest = max(shares, key=shares.get)
    points = [
        f"{fuelCost.CATEGORY_LABELS[biggest]} is the largest part of the fuel cycle cost: "
        f"${shares[biggest]:.2f} of every ${lc.lcoe:.2f}/MWh ({shares[biggest] / lc.lcoe:.0%})."
    ]
    if lc.reloads:
        costs = lc.costs[1:]
        mean = sum(c.total for c in costs) / len(costs)
        interval = lc.years / len(lc.cycles)
        what = "measured from the run" if lc.source == "run" else f"{lc.reloadFraction:.0%} of the core"
        points.append(
            f"After the {money(lc.costs[0].total)} initial core, {len(costs)} reloads ({what}) average {money(mean)} "
            f"each, about every {interval:.1f} years."
        )
    name, low, high, lowLabel, highLabel = lc.sensitivities()[0]
    points.append(
        f"The biggest lever is {leverName(name)}: {lowLabel} to {highLabel} moves the cost from ${low:.2f} "
        f"to ${high:.2f}/MWh."
    )
    noLead = withoutLeadTimes(lc)
    points.append(
        f"Buying fuel ahead of loading adds ${lc.lcoe - noLead:.2f}/MWh at a {lc.discountRate:.0%} discount rate; "
        f"undiscounted, the fuel costs ${lc.undiscountedLcoe:.2f}/MWh."
    )
    return points


def withoutLeadTimes(lc) -> float:
    """The levelized cost if every front-end step were paid the day its fuel is loaded."""
    return lc.repriced(replace(lc.unitCosts, leadTimes={})).lcoe


def groupAlike(summaries):
    """Runs of consecutive cycles that load the same fuel and make the same electricity."""

    def alike(a, b):
        return (
            math.isclose(a.frontEnd, b.frontEnd, rel_tol=1e-6, abs_tol=1e-3)
            and math.isclose(a.generation, b.generation, rel_tol=1e-6)
            and math.isclose(a.length, b.length, rel_tol=1e-6)
        )

    groups = []
    for s in summaries:
        if groups and alike(groups[-1][-1], s):
            groups[-1].append(s)
        else:
            groups.append([s])
    return groups


def leverName(name: str) -> str:
    """A sensitivity's input as it reads mid-sentence."""
    if name in fuelCost.CATEGORY_LABELS.values():
        return f"the {name[0].lower()}{name[1:]} price"
    return f"the {name.lower()}"


def basisLine(lc) -> str:
    """Power, capacity factor, cycles and discount rate on one line."""
    cycle = lc.cycles[0]
    return "  ·  ".join(
        [
            f"{lc.thermalPowerMW:,.0f} MWt",
            f"{lc.electricPowerMW:,.1f} MWe net",
            f"{lc.capacityFactor:.0%} capacity factor",
            f"{len(lc.cycles)} cycles of {cycle.length * cycle.capacityFactor:.2f} EFPY",
            f"{lc.discountRate:.0%} real discount rate",
        ]
    )


def unitPrice(category: str, core) -> str:
    """The price of one step, as it would be quoted."""
    u = core.unitCosts
    if category == "ore":
        return f"${u.orePerLbU3O8:.0f}/lb U3O8"
    if category == "conversion":
        return f"${u.conversionPerKgU:.0f}/kgU"
    if category == "enrichment":
        return f"${u.swu:.0f}/SWU"
    if category == "fabrication":
        prices = {u.fabricationPerKgHM[g.group.form] for g in core.groups}
        return f"${prices.pop():,.0f}/kgHM" if len(prices) == 1 else "by form"
    return f"${u.backEndPerMWh:.2f}/MWh"


def projectedText(lc) -> str:
    return f"projected, {lc.reloadFraction:.0%} of the core" if lc.reloads else "none"


def reloadsText(lc) -> str:
    """Where the reloads came from, as a phrase."""
    if lc.source == "run":
        return f"{len(lc.reloads)} batches of fresh fuel, as loaded by the run's fuel management"
    if not lc.reloads:
        return "none (one core for the whole life)"
    return f"projected as {lc.reloadFraction:.0%} of the initial core at the start of each later cycle"


def utilization(lc) -> list[tuple[str, str, str]]:
    """How well the fuel is used over the plant's life, every load included, as ``(name, value, unit)``."""
    loads = lc.costs
    heavyMetal = sum(c.heavyMetal for c in loads)
    natural = sum(c.oreUranium for c in loads)
    swu = sum(c.swu for c in loads)
    uranium = sum(c.uranium for c in loads)
    gwh = lc.totalGeneration / 1e3
    reloads = loads[1:] or loads
    reloadU = sum(c.uranium for c in reloads)
    reloadAssay = sum(c.assay * c.uranium for c in reloads) / reloadU if reloadU else 0.0
    feedPerProduct = sum(c.feed for c in reloads) / reloadU if reloadU else 0.0
    return [
        ("Discharge burnup", f"{lc.dischargeBurnup:,.1f}", "GWd/tHM, life average"),
        ("Specific power", f"{lc.thermalPowerMW / (loads[0].heavyMetal / 1e3):,.1f}", "kW/kgHM, initial core"),
        ("Reload enrichment", f"{100 * reloadAssay:.2f}", f"wt% U-235, feed/product {feedPerProduct:.1f}"),
        ("Fresh fuel", f"${sum(c.total for c in loads) / heavyMetal:,.0f}", "per kgHM, front end"),
        ("Natural uranium", f"{natural / gwh:,.2f}", "kgU per GWh(e)"),
        ("Separative work", f"{swu / gwh:,.2f}", "kg-SWU per GWh(e)"),
        ("Uranium utilization", f"{lc.thermalEnergyMWd / natural:,.2f}", "MWd(th) per kg natural U"),
        ("Fuel throughput", f"{heavyMetal / 1e3 / lc.years:,.2f}", f"tHM/y; {uranium / 1e3:,.1f} tU in all"),
    ]


def cumulativeSpend(lc, points: int = 600):
    """
    What has been paid by each moment, by category, undiscounted, in $M.

    Front-end steps are paid in lumps (their lead time before each load); spent fuel fees accrue with generation.

    Returns
    -------
    times : list of float
        Years from first startup, from the first payment to the end of the last cycle.
    paid : dict
        Category to the cumulative $M paid by each of ``times``.
    """
    flows = lc.purchases
    t0 = min(year for year, _ in flows)
    t1 = lc.years
    times = [t0 + (t1 - t0) * k / (points - 1) for k in range(points)]
    paid = {}
    for cat in fuelCost.COST_CATEGORIES:
        if cat == "backEnd":
            rate = lc.unitCosts.backEndPerMWh * lc.electricPowerMW * 24 * levelized.DAYS_PER_YEAR / 1e6
            paid[cat] = [
                sum(rate * c.capacityFactor * min(max(tt - c.start, 0.0), c.length) for c in lc.cycles) for tt in times
            ]
        else:
            cash = sorted(lc.cashFlows(cat))
            running, index, total, series = 0.0, 0, 0.0, []
            for tt in times:
                while index < len(cash) and cash[index][0] <= tt:
                    total += cash[index][1]
                    index += 1
                running = total / 1e6
                series.append(running)
            paid[cat] = series
    return times, paid


def waterfall(lc) -> list[tuple[str, float, float, str]]:
    """From fuel bought to levelized cost, as ``(step, start $/MWh, end $/MWh, note)``."""
    noLead = withoutLeadTimes(lc)
    return [
        ("Undiscounted", 0.0, lc.undiscountedLcoe, "every dollar paid over every MWh made"),
        ("Discounting", lc.undiscountedLcoe, noLead, f"fuel is paid before its electricity, at {lc.discountRate:.0%}"),
        ("Paying ahead of loading", noLead, lc.lcoe, "each step paid its lead time before loading"),
        ("Levelized fuel cycle cost", 0.0, lc.lcoe, ""),
    ]
