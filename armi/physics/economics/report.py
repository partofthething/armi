"""
PDF report of a core's fuel cost.

Three letter-size pages: a summary (headline numbers, the levelized fuel cost by category, the plant's operation and
the prices), the cost by fuel group with its breakdown table, and the cost per kg of fuel against enrichment.
Everything is laid out in inches on a fixed page so the report looks the same for any core.
"""

import datetime

import matplotlib.pyplot as plt
import numpy as np
from matplotlib.backends.backend_pdf import PdfPages
from matplotlib.lines import Line2D
from matplotlib.patches import FancyBboxPatch

from armi.physics.economics import fuelCost

#: One color per cost category, in a fixed order.
CATEGORY_COLORS = dict(zip(fuelCost.COST_CATEGORIES, ("#2a78d6", "#eb6834", "#1baf7a", "#eda100")))

INK = "#16181d"
SECONDARY = "#52514e"
MUTED = "#8a8984"
RULE = "#d9d8d3"
PANEL = "#f4f3ef"
ACCENT = "#1f4e8c"

PAGE_WIDTH, PAGE_HEIGHT = 8.5, 11.0
MARGIN = 0.75
CONTENT_WIDTH = PAGE_WIDTH - 2 * MARGIN

_STYLE = {
    "font.family": "sans-serif",
    # TrueType fonts only: pdf.fonttype 42 embeds them cleanly, where OpenType (CFF) fonts confuse some viewers.
    "font.sans-serif": ["Arial", "Liberation Sans", "DejaVu Sans"],
    "font.size": 9,
    "text.color": INK,
    "text.parse_math": False,
    "axes.labelcolor": SECONDARY,
    "axes.edgecolor": RULE,
    "axes.linewidth": 0.8,
    "axes.spines.top": False,
    "axes.spines.right": False,
    "axes.grid": True,
    "axes.axisbelow": True,
    "grid.color": "#ebeae6",
    "grid.linewidth": 0.6,
    "xtick.color": SECONDARY,
    "ytick.color": SECONDARY,
    "xtick.major.size": 0,
    "ytick.major.size": 0,
    "legend.frameon": False,
    "pdf.fonttype": 42,
}


def writeFuelCostReport(levelizedCost: fuelCost.FuelLevelizedCost, path: str, title: str = "") -> str:
    """Write the fuel cost report for ``levelizedCost`` to a PDF at ``path`` and return the path."""
    with plt.rc_context(_STYLE), PdfPages(path) as pdf:
        pages = [_summaryPage, _groupsPage, _unitCostPage]
        for number, makePage in enumerate(pages, start=1):
            page = _Page(title, number, len(pages))
            makePage(page, levelizedCost)
            pdf.savefig(page.fig)
            plt.close(page.fig)
        info = pdf.infodict()
        info["Title"] = f"Fuel cost report: {title}" if title else "Fuel cost report"
        info["Creator"] = "ARMI economics plugin"
    return path


class _Page:
    """A letter page addressed in inches from its bottom left corner, with a running header and footer."""

    def __init__(self, title, number, count):
        self.fig = plt.figure(figsize=(PAGE_WIDTH, PAGE_HEIGHT))
        self.text(MARGIN, PAGE_HEIGHT - 0.5, "FUEL COST REPORT", size=7.5, color=ACCENT, weight="bold")
        self.text(PAGE_WIDTH - MARGIN, PAGE_HEIGHT - 0.5, title, size=7.5, color=MUTED, ha="right")
        self.rule(PAGE_HEIGHT - 0.6, color=RULE)
        self.rule(0.6, color=RULE)
        today = datetime.date.today()
        self.text(MARGIN, 0.42, f"ARMI economics plugin  ·  {today:%B} {today.day}, {today.year}", size=7, color=MUTED)
        self.text(PAGE_WIDTH - MARGIN, 0.42, f"{number} / {count}", size=7, color=MUTED, ha="right")

    def text(self, x, y, s, **kwargs):
        kwargs.setdefault("va", "baseline")
        return self.fig.text(x / PAGE_WIDTH, y / PAGE_HEIGHT, s, **kwargs)

    def rule(self, y, x0=MARGIN, x1=PAGE_WIDTH - MARGIN, color=INK, width=0.6):
        self.fig.add_artist(
            Line2D([x0 / PAGE_WIDTH, x1 / PAGE_WIDTH], [y / PAGE_HEIGHT] * 2, color=color, linewidth=width)
        )

    def box(self, x, y, w, h, color=PANEL, rounded=True):
        self.fig.add_artist(
            FancyBboxPatch(
                (x / PAGE_WIDTH, y / PAGE_HEIGHT),
                w / PAGE_WIDTH,
                h / PAGE_HEIGHT,
                boxstyle="round,pad=0,rounding_size=0.008" if rounded else "square,pad=0",
                transform=self.fig.transFigure,
                facecolor=color,
                edgecolor="none",
            )
        )

    def axes(self, x, y, w, h):
        return self.fig.add_axes((x / PAGE_WIDTH, y / PAGE_HEIGHT, w / PAGE_WIDTH, h / PAGE_HEIGHT))

    def heading(self, y, s, note="", x0=MARGIN, x1=PAGE_WIDTH - MARGIN):
        """A section heading with a rule under it."""
        self.text(x0, y, s, size=11, weight="bold")
        if note:
            self.text(x1, y, note, size=8, color=MUTED, ha="right")
        self.rule(y - 0.1, x0, x1, color=INK, width=0.8)

    def pageTitle(self, s, subtitle=""):
        self.text(MARGIN, PAGE_HEIGHT - 1.15, s, size=20, weight="bold")
        if subtitle:
            self.text(MARGIN, PAGE_HEIGHT - 1.45, subtitle, size=10, color=SECONDARY)


def _summaryPage(page, lc):
    cost = lc.fuelCost
    page.pageTitle(
        "Initial core fuel cost", "Front-end fuel cost of the fresh core and its levelized cost of electricity"
    )

    # headline numbers
    tiles = [
        ("Initial core", f"${cost.total / 1e6:,.1f}M", "overnight, all fuel"),
        ("Levelized fuel cost", f"${lc.lcoe:.2f}", f"per MWh at {100 * lc.discountRate:.0f}% discount"),
        ("Undiscounted", f"${lc.undiscountedLcoe:.2f}", "per MWh"),
        ("Unit cost", f"${cost.total / cost.heavyMetal:,.0f}", "per kg heavy metal"),
    ]
    gap = 0.15
    tileWidth = (CONTENT_WIDTH - gap * (len(tiles) - 1)) / len(tiles)
    top = PAGE_HEIGHT - 1.75
    for i, (label, value, note) in enumerate(tiles):
        x = MARGIN + i * (tileWidth + gap)
        page.box(x, top - 1.0, tileWidth, 1.0)
        page.text(x + 0.15, top - 0.28, label.upper(), size=7, color=SECONDARY, weight="bold")
        page.text(x + 0.15, top - 0.66, value, size=19, weight="bold", color=ACCENT if i == 1 else INK)
        page.text(x + 0.15, top - 0.86, note, size=7.5, color=MUTED)

    # levelized cost by category
    y = top - 1.45
    page.heading(y, "Levelized fuel cost by category", f"$/MWh, discounted at {100 * lc.discountRate:.0f}%")
    byCategory = lc.lcoeByCategory()
    ax = page.axes(MARGIN + 1.75, y - 2.0, CONTENT_WIDTH - 1.75, 1.65)
    labels = [fuelCost.CATEGORY_LABELS[cat] for cat in byCategory]
    values = list(byCategory.values())
    ax.barh(labels, values, height=0.58, color=[CATEGORY_COLORS[cat] for cat in byCategory])
    for i, value in enumerate(values):
        ax.annotate(
            f"${value:.2f}  ·  {value / lc.lcoe:.0%}",
            (value, i),
            xytext=(6, 0),
            textcoords="offset points",
            va="center",
            size=8.5,
        )
    ax.invert_yaxis()
    ax.set_xlim(0, max(values) * 1.3)
    ax.grid(axis="y", visible=False)
    ax.spines["left"].set_visible(False)
    ax.tick_params(axis="y", labelsize=9, labelcolor=INK, pad=8)
    ax.tick_params(axis="x", labelsize=7.5)
    ax.xaxis.set_major_formatter(lambda v, _: f"${v:,.0f}")
    ax.set_xlabel(f"$/MWh  (total ${lc.lcoe:.2f})", size=8)

    # operation and prices, side by side
    y -= 2.75
    columnWidth = (CONTENT_WIDTH - 0.4) / 2
    cycle = lc.cycles[0]
    uniform = all((c.length, c.capacityFactor) == (cycle.length, cycle.capacityFactor) for c in lc.cycles)
    operation = [
        ("Thermal power", f"{lc.thermalPowerMW:,.1f} MWt"),
        ("Net thermal efficiency", f"{lc.thermalEfficiency:.1%}"),
        ("Net electric power", f"{lc.electricPowerMW:,.1f} MWe"),
        ("Capacity factor", f"{lc.capacityFactor:.1%}" + ("" if uniform else " (average)")),
        ("Cycles", f"{len(lc.cycles)}"),
        (
            "Cycle length",
            f"{cycle.length:.2f} y, {cycle.length * cycle.capacityFactor:.2f} EFPY"
            if uniform
            else f"{lc.years / len(lc.cycles):.2f} y average",
        ),
        ("Operating life", f"{lc.years:.1f} y, {lc.fullPowerYears:.1f} EFPY"),
        ("Reload per cycle", f"{lc.reloadFraction:.0%} of the core"),
        ("Discount rate", f"{lc.discountRate:.1%}"),
    ]
    u = cost.unitCosts
    prices = [
        ("Natural uranium", f"${u.orePerLbU3O8:.0f}/lb U3O8  (${u.orePerKgU:.0f}/kgU)"),
        ("Conversion", f"${u.conversionPerKgU:.0f}/kgU, {u.conversionLoss:.1%} loss"),
        ("Enrichment", f"${u.swu:.0f}/SWU"),
        ("Feed assay", f"{100 * u.feedAssay:.3f} wt% U-235"),
        ("Tails assay", f"{100 * u.tailsAssay:.3f} wt% U-235"),
        *((f"Fabrication, {form}", f"${price:,.0f}/kgHM") for form, price in u.fabricationPerKgHM.items()),
    ]
    _keyValues(page, MARGIN, y, columnWidth, "Plant and operation", operation)
    _keyValues(page, MARGIN + columnWidth + 0.4, y, columnWidth, "Prices and assumptions", prices)

    # material quantities
    y -= 0.36 + 0.27 * max(len(operation), len(prices)) + 0.45
    page.heading(y, "Material for the initial core")
    material = [
        ("Heavy metal", f"{cost.heavyMetal:,.0f} kg"),
        ("Natural uranium", f"{cost.oreUranium:,.0f} kg U"),
        ("Separative work", f"{cost.swu:,.0f} kg-SWU"),
    ]
    width = CONTENT_WIDTH / len(material)
    for i, (label, value) in enumerate(material):
        page.text(MARGIN + i * width, y - 0.42, label, size=8, color=SECONDARY)
        page.text(MARGIN + i * width, y - 0.7, value, size=13, weight="bold")


def _keyValues(page, x, y, width, heading, rows):
    page.heading(y, heading, x0=x, x1=x + width)
    for i, (key, value) in enumerate(rows):
        rowY = y - 0.36 - 0.27 * i
        page.text(x, rowY, key, size=8.5, color=SECONDARY)
        page.text(x + width, rowY, value, size=8.5, ha="right")
        page.rule(rowY - 0.09, x, x + width, color=RULE, width=0.4)


def _groupsPage(page, lc):
    cost = lc.fuelCost
    page.pageTitle("Cost by fuel group", "Each fuel form and enrichment in the initial core")

    # stacked bars
    y = PAGE_HEIGHT - 2.05
    page.heading(y, "Overnight cost", "$M")
    labels = [_groupLabel(g.group) for g in cost.groups]
    height = max(1.6, 0.42 * len(labels) + 0.6)
    labelWidth = 1.45
    ax = page.axes(MARGIN + labelWidth, y - 0.7 - height, CONTENT_WIDTH - labelWidth, height)
    left = np.zeros(len(cost.groups))
    for cat in fuelCost.COST_CATEGORIES:
        values = np.array([g.costs[cat] / 1e6 for g in cost.groups])
        ax.barh(
            labels,
            values,
            left=left,
            color=CATEGORY_COLORS[cat],
            label=fuelCost.CATEGORY_LABELS[cat],
            height=0.62,
            edgecolor="white",
            linewidth=1.2,
        )
        left += values
    for i, total in enumerate(left):
        ax.annotate(f"${total:,.1f}M", (total, i), xytext=(5, 0), textcoords="offset points", va="center", size=8)
    ax.invert_yaxis()
    ax.grid(axis="y", visible=False)
    ax.spines["left"].set_visible(False)
    ax.tick_params(axis="y", labelcolor=INK, pad=8)
    ax.xaxis.set_major_formatter(lambda v, _: f"${v:,.0f}M")
    ax.set_xlim(0, left.max() * 1.15)
    ax.legend(ncols=4, loc="lower left", bbox_to_anchor=(-labelWidth / (CONTENT_WIDTH - labelWidth), 1.03), fontsize=8)

    # breakdown table
    y = y - 0.7 - height - 0.7
    page.heading(y, "Breakdown")
    columns = [
        ("Fuel group", "left", 1.35),
        ("Heavy metal\nkg", "right", 0.8),
        ("Natural U\nkg", "right", 0.8),
        ("Separative\nkg-SWU", "right", 0.8),
        ("Natural U\n$M", "right", 0.7),
        ("Conversion\n$M", "right", 0.75),
        ("Enrichment\n$M", "right", 0.75),
        ("Fabrication\n$M", "right", 0.75),
        ("Total\n$M", "right", 0.55),
    ]
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
    categories = cost.byCategory()
    total = [
        "Total",
        f"{cost.heavyMetal:,.0f}",
        f"{cost.oreUranium:,.0f}",
        f"{cost.swu:,.0f}",
        *(f"{categories[cat] / 1e6:,.2f}" for cat in fuelCost.COST_CATEGORIES),
        f"{cost.total / 1e6:,.2f}",
    ]
    _table(page, y - 0.2, columns, rows, total)


def _table(page, top, columns, rows, total):
    """Draw a table with a two-line header, banded rows and a bold total row."""
    scale = CONTENT_WIDTH / sum(width for _, _, width in columns)
    edges = np.cumsum([0.0] + [width * scale for _, _, width in columns]) + MARGIN
    rowHeight = 0.27
    pad = 0.08

    def cells(y, values, **kwargs):
        for (_, align, _), x0, x1, value in zip(columns, edges[:-1], edges[1:], values):
            page.text(x0 + pad if align == "left" else x1 - pad, y, value, ha=align, **kwargs)

    headerBottom = top - 0.42
    cells(headerBottom + 0.06, [name for name, _, _ in columns], size=7.5, color=SECONDARY, weight="bold", va="bottom")
    page.rule(headerBottom, color=INK, width=0.8)
    y = headerBottom
    for i, row in enumerate(rows):
        if i % 2:
            page.box(MARGIN, y - rowHeight, CONTENT_WIDTH, rowHeight, rounded=False)
        cells(y - rowHeight / 2, row, size=8.5, va="center")
        y -= rowHeight
    page.rule(y, color=INK, width=0.8)
    cells(y - rowHeight / 2, total, size=8.5, weight="bold", va="center")
    page.rule(y - rowHeight, color=INK, width=0.8)


def _unitCostPage(page, lc):
    """Cost per kg of fuel against enrichment, by category, with the core's groups marked."""
    cost = lc.fuelCost
    u = cost.unitCosts
    page.pageTitle("Cost per kg of fuel", "What a kg of heavy metal costs at these prices, against its enrichment")
    forms = sorted({g.group.form for g in cost.groups})
    maxAssay = max(0.06, max(g.group.assay for g in cost.groups) * 1.2)
    assays = np.linspace(u.feedAssay, maxAssay, 200)
    available = PAGE_HEIGHT - 2.05 - 1.2
    panelHeight = min(4.2, available / len(forms))
    for i, form in enumerate(forms):
        y = PAGE_HEIGHT - 2.05 - i * panelHeight
        page.heading(y, form, "$ per kg heavy metal")
        ax = page.axes(MARGIN + 0.6, y - panelHeight + 0.6, CONTENT_WIDTH - 0.6, panelHeight - 1.15)
        perKg = [_costPerKg(form, x, u) for x in assays]
        lower = np.zeros_like(assays)
        for cat in fuelCost.COST_CATEGORIES:
            upper = lower + [c[cat] for c in perKg]
            ax.fill_between(
                100 * assays,
                lower,
                upper,
                color=CATEGORY_COLORS[cat],
                label=fuelCost.CATEGORY_LABELS[cat],
                linewidth=0,
            )
            lower = upper
        for g in (g for g in cost.groups if g.group.form == form):
            x, perKgHM = 100 * g.group.assay, g.total / g.group.heavyMetal
            ax.plot(x, perKgHM, "o", color=INK, markersize=6, markeredgecolor="white", markeredgewidth=1.8)
            ax.annotate(
                f"${perKgHM:,.0f}", (x, perKgHM), xytext=(0, 8), textcoords="offset points", ha="center", size=7.5
            )
        ax.set_xlabel("Enrichment, wt% U-235", size=8)
        ax.set_xlim(100 * assays[0], 100 * assays[-1])
        ax.set_ylim(0, lower.max() * 1.1)
        ax.yaxis.set_major_formatter(lambda v, _: f"${v:,.0f}")
        ax.legend(ncols=4, loc="lower left", bbox_to_anchor=(0, 1.0), fontsize=8)


def _costPerKg(form, assay, unitCosts):
    """Cost of one kg of uranium fuel of ``form`` at ``assay``, by category."""
    return fuelCost.costFuel([fuelCost.FuelGroup(form, 1.0, 1.0, assay)], unitCosts).groups[0].costs


def _groupLabel(group):
    return f"{group.form}, {100 * group.assay:.2f} wt%"
