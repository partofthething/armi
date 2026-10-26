"""PDF report of a core's fuel cost."""

import matplotlib.pyplot as plt
import numpy as np
from matplotlib.backends.backend_pdf import PdfPages

from armi.physics.economics import fuelCost

#: One color per cost category, in a fixed order.
CATEGORY_COLORS = dict(zip(fuelCost.COST_CATEGORIES, ("#2a78d6", "#eb6834", "#1baf7a", "#eda100")))

CATEGORY_LABELS = {
    "ore": "Ore (U3O8)",
    "conversion": "Conversion",
    "enrichment": "Enrichment",
    "fabrication": "Fabrication",
}

TEXT = "#0b0b0b"
MUTED = "#52514e"
GRID = "#e4e3df"


def writeFuelCostReport(levelizedCost: fuelCost.FuelLevelizedCost, path: str) -> str:
    """Write the fuel cost summary, breakdown and unit cost curve to a PDF at ``path``, and return it."""
    with plt.rc_context(_STYLE), PdfPages(path) as pdf:
        pdf.savefig(_summaryPage(levelizedCost))
        pdf.savefig(_unitCostPage(levelizedCost.fuelCost))
        plt.close("all")
    return path


_STYLE = {
    "font.size": 9,
    "text.color": TEXT,
    "axes.labelcolor": TEXT,
    "axes.edgecolor": MUTED,
    "axes.spines.top": False,
    "axes.spines.right": False,
    "axes.grid": True,
    "axes.axisbelow": True,
    "grid.color": GRID,
    "xtick.color": MUTED,
    "ytick.color": MUTED,
    "pdf.fonttype": 42,
    "text.parse_math": False,
}


def _summaryPage(levelizedCost):
    cost = levelizedCost.fuelCost
    fig = plt.figure(figsize=(8.5, 11))
    fig.suptitle("Initial core fuel cost", x=0.08, ha="left", fontsize=15, fontweight="bold")

    categories = cost.byCategory()
    lines = [
        f"Overnight cost of the initial core: ${cost.total / 1e6:,.2f}M",
        f"{cost.heavyMetal:,.0f} kg heavy metal, ${cost.total / cost.heavyMetal:,.0f}/kgHM",
        f"{cost.oreUranium:,.0f} kg natural uranium, {cost.swu:,.0f} kg-SWU",
        f"Levelized fuel cost: ${levelizedCost.lcoe:.2f}/MWh at {100 * levelizedCost.discountRate:.1f}% "
        f"(${levelizedCost.undiscountedLcoe:.2f}/MWh undiscounted)",
        f"{len(levelizedCost.cycles)} cycle(s) over {sum(c.length for c in levelizedCost.cycles):.2f} years at "
        f"{levelizedCost.electricPowerMW:.1f} MWe, reloading {levelizedCost.reloadFraction:.0%} of the core per cycle",
    ]
    fig.text(0.08, 0.935, "\n".join(lines), va="top", linespacing=1.6)

    ax = fig.add_axes([0.24, 0.55, 0.68, 0.22])
    labels = [_groupLabel(g.group) for g in cost.groups]
    left = np.zeros(len(cost.groups))
    for cat in fuelCost.COST_CATEGORIES:
        values = np.array([g.costs[cat] / 1e6 for g in cost.groups])
        ax.barh(
            labels,
            values,
            left=left,
            color=CATEGORY_COLORS[cat],
            label=CATEGORY_LABELS[cat],
            height=0.6,
            edgecolor="white",
            linewidth=1,
        )
        left += values
    for y, total in enumerate(left):
        ax.annotate(f"${total:,.1f}M", (total, y), xytext=(4, 0), textcoords="offset points", va="center")
    ax.invert_yaxis()
    ax.grid(axis="y", visible=False)
    ax.set_xlabel("Cost, $M")
    fig.text(0.08, 0.835, "By fuel group", fontweight="bold")
    ax.legend(ncols=4, loc="lower left", bbox_to_anchor=(-0.24, 1.08), frameon=False)
    ax.set_xlim(right=left.max() * 1.15)

    rows = [
        [
            _groupLabel(g.group),
            f"{g.group.heavyMetal:,.0f}",
            f"{g.oreUranium:,.0f}",
            f"{g.requirement.swu:,.0f}",
            *(f"{g.costs[cat] / 1e6:,.2f}" for cat in fuelCost.COST_CATEGORIES),
            f"{g.total / 1e6:,.2f}",
        ]
        for g in cost.groups
    ]
    rows.append(
        [
            "Total",
            f"{cost.heavyMetal:,.0f}",
            f"{cost.oreUranium:,.0f}",
            f"{cost.swu:,.0f}",
            *(f"{categories[cat] / 1e6:,.2f}" for cat in fuelCost.COST_CATEGORIES),
            f"{cost.total / 1e6:,.2f}",
        ]
    )
    columns = ["Group", "kgHM", "Ore kgU", "kg-SWU", "Ore $M", "Conv. $M", "SWU $M", "Fab. $M", "Total $M"]
    tableAx = fig.add_axes([0.08, 0.08, 0.84, 0.38])
    tableAx.axis("off")
    tableAx.set_title("Breakdown", loc="left", fontweight="bold")
    table = tableAx.table(
        cellText=rows,
        colLabels=columns,
        colWidths=[0.2] + [0.1] * (len(columns) - 1),
        loc="upper left",
        cellLoc="right",
        colLoc="right",
    )
    table.auto_set_font_size(False)
    table.set_fontsize(8)
    table.scale(1, 1.4)
    for (row, _col), cell in table.get_celld().items():
        cell.set_edgecolor(GRID)
        if row == 0 or row == len(rows):
            cell.set_text_props(fontweight="bold")

    u = cost.unitCosts
    fabrication = ", ".join(f"{form} ${price:,.0f}/kgHM" for form, price in u.fabricationPerKgHM.items())
    fig.text(
        0.08,
        0.04,
        f"Prices: ore ${u.orePerLbU3O8:.0f}/lb U3O8, conversion ${u.conversionPerKgU:.0f}/kgU "
        f"({100 * u.conversionLoss:.1f}% loss), enrichment ${u.swu:.0f}/SWU, feed {100 * u.feedAssay:.3f} wt%, "
        f"tails {100 * u.tailsAssay:.3f} wt%.\nFabrication: {fabrication}.",
        color=MUTED,
        fontsize=8,
        va="bottom",
    )
    return fig


def _unitCostPage(cost):
    """Cost per kg of fuel against enrichment, by category, with the core's groups marked."""
    u = cost.unitCosts
    forms = sorted({g.group.form for g in cost.groups})
    height = 1.6 + 3.4 * len(forms)
    fig, axes = plt.subplots(len(forms), 1, figsize=(8.5, height), squeeze=False)
    fig.suptitle(
        "Fuel cost per kg of heavy metal",
        x=0.08,
        ha="left",
        fontsize=15,
        fontweight="bold",
        y=1 - 0.35 / height,
        va="top",
    )
    maxAssay = max(0.06, max(g.group.assay for g in cost.groups) * 1.2)
    assays = np.linspace(u.feedAssay, maxAssay, 200)
    for ax, form in zip(axes[:, 0], forms):
        perKg = [_costPerKg(form, x, u) for x in assays]
        stacked = np.cumsum([[c[cat] for c in perKg] for cat in fuelCost.COST_CATEGORIES], axis=0)
        lower = np.zeros_like(assays)
        for cat, upper in zip(fuelCost.COST_CATEGORIES, stacked):
            ax.fill_between(
                100 * assays, lower, upper, color=CATEGORY_COLORS[cat], label=CATEGORY_LABELS[cat], linewidth=0
            )
            lower = upper
        for g in (g for g in cost.groups if g.group.form == form):
            x, y = 100 * g.group.assay, g.total / g.group.heavyMetal
            ax.plot(x, y, "o", color=TEXT, markersize=6, markeredgecolor="white", markeredgewidth=2)
            ax.annotate(f"${y:,.0f}", (x, y), xytext=(0, 7), textcoords="offset points", ha="center", fontsize=8)
        ax.set_title(form, loc="left", fontweight="bold")
        ax.set_xlabel("Enrichment, wt% U-235")
        ax.set_ylabel("$/kgHM")
        ax.set_xlim(100 * assays[0], 100 * assays[-1])
        ax.set_ylim(bottom=0)
    axes[0, 0].legend(ncols=4, loc="lower left", bbox_to_anchor=(0, 1.1), frameon=False)
    fig.subplots_adjust(left=0.12, right=0.92, top=1 - 1.3 / height, bottom=0.6 / height, hspace=0.6)
    return fig


def _costPerKg(form, assay, unitCosts):
    """Cost of one kg of uranium fuel of ``form`` at ``assay``, by category."""
    return fuelCost.costFuel([fuelCost.FuelGroup(form, 1.0, 1.0, assay)], unitCosts).groups[0].costs


def _groupLabel(group):
    return f"{group.form}, {100 * group.assay:.2f} wt%"
