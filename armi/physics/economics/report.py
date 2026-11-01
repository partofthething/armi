"""
PDF report of a plant's fuel cycle cost, written for the people deciding whether to build and fuel it.

Four letter-size pages, the first for everyone, the rest for whoever needs the detail:

1. **Summary**: the headline numbers, the initial core traced from the mine to spent fuel (feed, conversion,
   separative work, tails, fabrication, electricity) with every step's quantity and cost, where each $/MWh goes, a
   table of every step for the initial core, the plant's life and per MWh, and computed key points.
2. **Reloads and fuel utilization**: discharge burnup, uranium and separative work per unit of electricity, the fuel
   bought for each load, and each cycle's fresh fuel against the electricity it makes.
3. **Cash flow and sensitivity**: the cumulative spend as paid, and what moves the levelized cost.
4. **Basis and method**: the initial core by fuel group, every price and assumption, and the equations.

Everything is laid out in inches on a fixed page so the report looks the same for any core.
"""

import datetime
import math
from dataclasses import replace

import matplotlib.pyplot as plt
import numpy as np
from matplotlib.backends.backend_pdf import PdfPages
from matplotlib.lines import Line2D
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch, Rectangle
from matplotlib.ticker import MaxNLocator

from armi.physics.economics import fuelCost

#: One color per cost category, in a fixed order (a CVD-checked categorical palette).
CATEGORY_COLORS = dict(zip(fuelCost.COST_CATEGORIES, ("#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4")))

#: Short names of the cost categories for legends and narrow columns.
SHORT_LABELS = {
    "ore": "Natural U",
    "conversion": "Conversion",
    "enrichment": "Enrichment",
    "fabrication": "Fabrication",
    "backEnd": "Spent fuel",
}

#: Very short names of the front-end steps, for the lead-time row.
LEAD_LABELS = {"ore": "U3O8", "conversion": "UF6", "enrichment": "SWU", "fabrication": "fab."}

INK = "#0b0b0b"
SECONDARY = "#52514e"
MUTED = "#898781"
GRID = "#e1e0d9"
AXIS = "#c3c2b7"
PANEL = "#f4f3ef"
ACCENT = "#1f4e8c"
LOWER = "#2a78d6"
HIGHER = "#e34948"
WHITE = "#ffffff"

PAGE_WIDTH, PAGE_HEIGHT = 8.5, 11.0
MARGIN = 0.75
CONTENT_WIDTH = PAGE_WIDTH - 2 * MARGIN
RIGHT = PAGE_WIDTH - MARGIN
ROW = 0.24

#: Most cycles listed in the cycle table; longer lives are summed in a final row.
MAX_CYCLE_ROWS = 10

_STYLE = {
    "font.family": "sans-serif",
    # TrueType fonts only: pdf.fonttype 42 embeds them cleanly, where OpenType (CFF) fonts confuse some viewers.
    "font.sans-serif": ["Roboto", "Arial", "Liberation Sans", "DejaVu Sans"],
    "font.size": 8.5,
    "text.color": INK,
    "text.parse_math": False,
    "axes.labelcolor": SECONDARY,
    "axes.edgecolor": AXIS,
    "axes.linewidth": 0.7,
    "axes.spines.top": False,
    "axes.spines.right": False,
    "axes.spines.left": False,
    "axes.grid": True,
    "axes.axisbelow": True,
    "grid.color": GRID,
    "grid.linewidth": 0.5,
    "xtick.color": MUTED,
    "ytick.color": MUTED,
    "xtick.labelsize": 7.5,
    "ytick.labelsize": 7.5,
    "xtick.major.size": 0,
    "ytick.major.size": 0,
    "xtick.major.pad": 5,
    "ytick.major.pad": 5,
    "legend.frameon": False,
    "pdf.fonttype": 42,
}


def writeFuelCostReport(lc: fuelCost.FuelCycleCost, path: str, title: str = "") -> str:
    """Write the fuel cycle cost report for ``lc`` to a PDF at ``path`` and return the path."""
    with plt.rc_context(_STYLE), PdfPages(path) as pdf:
        pages = [_summaryPage, _reloadsPage, _cashFlowPage, _basisPage]
        for number, makePage in enumerate(pages, start=1):
            page = _Page(title, number, len(pages))
            makePage(page, lc)
            pdf.savefig(page.fig)
            plt.close(page.fig)
        info = pdf.infodict()
        info["Title"] = f"Fuel cycle cost: {title}" if title else "Fuel cycle cost"
        info["Creator"] = "ARMI economics plugin"
    return path


class _Page:
    """A letter page addressed in inches from its bottom left corner, with a running header and footer."""

    def __init__(self, title, number, count):
        self.fig = plt.figure(figsize=(PAGE_WIDTH, PAGE_HEIGHT))
        self.fig.patch.set_facecolor(WHITE)
        self.box(0, PAGE_HEIGHT - 0.12, PAGE_WIDTH, 0.12, color=ACCENT, rounded=False)
        self.text(MARGIN, PAGE_HEIGHT - 0.5, "FUEL CYCLE COST", size=7, color=ACCENT, weight="bold")
        self.text(RIGHT, PAGE_HEIGHT - 0.5, title, size=7, color=MUTED, ha="right")
        self.rule(0.62, color=GRID)
        today = datetime.date.today()
        self.text(
            MARGIN, 0.42, f"ARMI economics plugin  ·  {today:%B} {today.day}, {today.year}", size=6.5, color=MUTED
        )
        self.text(RIGHT, 0.42, f"{number} / {count}", size=6.5, color=MUTED, ha="right")

    def text(self, x, y, s, **kwargs):
        kwargs.setdefault("va", "baseline")
        return self.fig.text(x / PAGE_WIDTH, y / PAGE_HEIGHT, s, **kwargs)

    def rule(self, y, x0=MARGIN, x1=RIGHT, color=INK, width=0.6):
        self.fig.add_artist(
            Line2D([x0 / PAGE_WIDTH, x1 / PAGE_WIDTH], [y / PAGE_HEIGHT] * 2, color=color, linewidth=width)
        )

    def box(self, x, y, w, h, color=PANEL, rounded=True, radius=0.06):
        self.fig.add_artist(
            FancyBboxPatch(
                (x / PAGE_WIDTH, y / PAGE_HEIGHT),
                w / PAGE_WIDTH,
                h / PAGE_HEIGHT,
                boxstyle=f"round,pad=0,rounding_size={radius / PAGE_WIDTH}" if rounded else "square,pad=0",
                mutation_aspect=PAGE_WIDTH / PAGE_HEIGHT,
                transform=self.fig.transFigure,
                facecolor=color,
                edgecolor="none",
            )
        )

    def arrow(self, x0, x1, y, color=AXIS):
        self.fig.add_artist(
            FancyArrowPatch(
                (x0 / PAGE_WIDTH, y / PAGE_HEIGHT),
                (x1 / PAGE_WIDTH, y / PAGE_HEIGHT),
                transform=self.fig.transFigure,
                arrowstyle="-|>,head_length=4,head_width=2.4",
                color=color,
                linewidth=1.0,
                shrinkA=0,
                shrinkB=0,
            )
        )

    def axes(self, x, y, w, h):
        return self.fig.add_axes((x / PAGE_WIDTH, y / PAGE_HEIGHT, w / PAGE_WIDTH, h / PAGE_HEIGHT))

    def heading(self, y, s, note="", x0=MARGIN, x1=RIGHT):
        """A section heading with a hairline under it."""
        self.text(x0, y, s, size=10.5, weight="bold")
        if note:
            self.text(x1, y, note, size=7.5, color=MUTED, ha="right")
        self.rule(y - 0.1, x0, x1, color=INK, width=0.7)

    def pageTitle(self, s, subtitle=""):
        self.text(MARGIN, PAGE_HEIGHT - 1.12, s, size=21, weight="bold")
        if subtitle:
            self.text(MARGIN, PAGE_HEIGHT - 1.4, subtitle, size=9, color=SECONDARY)

    def tiles(self, top, tiles, accent=1, height=0.95):
        """A row of headline numbers, each ``(label, value, note)``; return the y of their bottom."""
        gap = 0.14
        width = (CONTENT_WIDTH - gap * (len(tiles) - 1)) / len(tiles)
        for i, (label, value, note) in enumerate(tiles):
            x = MARGIN + i * (width + gap)
            highlight = i == accent
            self.box(x, top - height, width, height, color=ACCENT if highlight else PANEL)
            labelColor, valueColor, noteColor = ("#c9d6ea", WHITE, "#c9d6ea") if highlight else (SECONDARY, INK, MUTED)
            self.text(x + 0.15, top - 0.27, label.upper(), size=6.5, color=labelColor, weight="bold")
            self.text(x + 0.15, top - 0.62, value, size=19, weight="bold", color=valueColor)
            self.text(x + 0.15, top - 0.8, note, size=7, color=noteColor)
        return top - height

    def metricGrid(self, top, metrics, columns=4, height=0.72):
        """Small panels of ``(label, value, unit)``, ``columns`` to a row."""
        gap = 0.12
        width = (CONTENT_WIDTH - gap * (columns - 1)) / columns
        for i, (label, value, unit) in enumerate(metrics):
            x = MARGIN + (i % columns) * (width + gap)
            y = top - (i // columns) * (height + gap)
            self.box(x, y - height, width, height)
            self.text(x + 0.12, y - 0.22, label.upper(), size=6.3, color=SECONDARY, weight="bold")
            self.text(x + 0.12, y - 0.48, value, size=14, weight="bold")
            self.text(x + 0.12, y - 0.63, unit, size=6.8, color=MUTED)

    def table(self, top, columns, rows, total=None, swatches=None):
        """
        A table with a two-line header, banded rows and an optional bold total row; return the y of its bottom.

        ``columns`` are ``(header, align, relative width)``; ``swatches`` puts a color chip before each row's first
        cell.
        """
        scale = CONTENT_WIDTH / sum(width for _, _, width in columns)
        edges = np.cumsum([0.0] + [width * scale for _, _, width in columns]) + MARGIN
        pad = 0.08

        def cells(y, values, indent=0.0, **kwargs):
            for i, ((_, align, _), x0, x1, value) in enumerate(zip(columns, edges[:-1], edges[1:], values)):
                x = x0 + pad + (indent if i == 0 else 0.0) if align == "left" else x1 - pad
                self.text(x, y, value, ha=align, **kwargs)

        headerBottom = top - 0.36
        cells(
            headerBottom + 0.06, [name for name, _, _ in columns], size=6.8, color=SECONDARY, weight="bold", va="bottom"
        )
        self.rule(headerBottom, color=INK, width=0.7)
        y = headerBottom
        for i, row in enumerate(rows):
            if i % 2:
                self.box(MARGIN, y - ROW, CONTENT_WIDTH, ROW, rounded=False)
            if swatches:
                self.box(MARGIN + pad, y - ROW / 2 - 0.045, 0.09, 0.09, color=swatches[i], radius=0.015)
            cells(y - ROW / 2, row, indent=0.17 if swatches else 0.0, size=8, va="center")
            y -= ROW
        self.rule(y, color=INK, width=0.7)
        if total is not None:
            cells(y - ROW / 2, total, indent=0.17 if swatches else 0.0, size=8, weight="bold", va="center")
            y -= ROW
            self.rule(y, color=INK, width=0.7)
        return y

    def keyValues(self, x, y, width, heading, rows):
        """A heading and a column of ``(key, value)`` rows; return the y of its bottom."""
        self.heading(y, heading, x0=x, x1=x + width)
        for i, (key, value) in enumerate(rows):
            rowY = y - 0.33 - ROW * i
            self.text(x, rowY, key, size=8, color=SECONDARY)
            self.text(x + width, rowY, value, size=8, ha="right")
            self.rule(rowY - 0.08, x, x + width, color=GRID, width=0.4)
        return y - 0.33 - ROW * len(rows)

    def note(self, y, s, x=MARGIN):
        self.text(x, y, s, size=7, color=MUTED)


def _summaryPage(page, lc):
    core = lc.fuelCost
    first = lc.cycleSummaries()[0]
    page.pageTitle("Fuel cycle cost", _basisLine(lc))
    y = page.tiles(
        PAGE_HEIGHT - 1.7,
        [
            ("Initial core", _money(core.total), f"fresh fuel, {core.heavyMetal / 1e3:,.1f} tHM"),
            ("Fuel cycle cost", f"${lc.lcoe:.2f}", f"per MWh, levelized at {lc.discountRate:.0%} real"),
            ("Plant-life fuel", _money(lc.totalSpend), f"{len(lc.charges)} loads + spent fuel, undiscounted"),
            ("Electricity", f"{lc.totalGeneration / 1e6:,.1f} TWh", f"net, over {lc.years:.0f} years"),
        ],
    )

    # the initial core from the mine to spent fuel
    y -= 0.4
    page.heading(y, "Initial core, mine to spent fuel", "quantities and cost for the initial core and its first cycle")
    initial = core.byCategory()
    u = core.unitCosts
    nodes = [
        (
            "ore",
            "Natural uranium",
            f"{core.u3o8Lb / 1e3:,.0f}",
            "klb U3O8",
            f"{core.oreUranium / 1e3:,.1f} tU",
            initial["ore"],
        ),
        (
            "conversion",
            "Conversion",
            f"{core.feed / 1e3:,.1f}",
            "tU as UF6",
            f"{u.conversionLoss:.1%} loss",
            initial["conversion"],
        ),
        (
            "enrichment",
            "Enrichment",
            f"{core.swu / 1e3:,.1f}",
            "tSWU",
            f"{core.swu / core.uranium:,.2f} SWU/kgU",
            initial["enrichment"],
        ),
        (
            "fabrication",
            "Fabrication",
            f"{core.heavyMetal / 1e3:,.2f}",
            "tHM",
            f"{100 * core.assay:.2f} wt% U-235",
            initial["fabrication"],
        ),
        (
            None,
            "Reactor",
            f"{first.generation / 1e3:,.0f}",
            "GWh, cycle 1",
            f"{lc.cycles[0].length * lc.cycles[0].capacityFactor:.2f} EFPY",
            None,
        ),
        (
            "backEnd",
            "Spent fuel",
            f"${u.backEndPerMWh:.2f}",
            "per MWh generated",
            "storage and disposal",
            first.backEnd,
        ),
    ]
    gap = 0.2
    cardWidth = (CONTENT_WIDTH - gap * (len(nodes) - 1)) / len(nodes)
    top, height = y - 0.26, 1.24
    for i, (cat, step, value, unit, detail, dollars) in enumerate(nodes):
        x = MARGIN + i * (cardWidth + gap)
        color = CATEGORY_COLORS[cat] if cat else INK
        page.box(x, top - height, cardWidth, height)
        page.box(x, top - 0.05, cardWidth, 0.05, color=color, rounded=False)
        page.text(x + 0.1, top - 0.27, step.upper(), size=6.5, color=SECONDARY, weight="bold")
        page.text(x + 0.1, top - 0.6, value, size=15, weight="bold")
        page.text(x + 0.1, top - 0.77, unit, size=7, color=SECONDARY)
        page.text(x + 0.1, top - 0.94, detail, size=6.5, color=MUTED)
        page.rule(top - 1.04, x + 0.1, x + cardWidth - 0.1, color=GRID, width=0.5)
        if dollars is None:
            page.text(x + 0.1, top - 1.2, "makes the electricity", size=6.5, color=MUTED)
        else:
            page.text(x + 0.1, top - 1.21, f"${dollars / 1e6:,.2f}M", size=9.5, weight="bold")
        if i < len(nodes) - 1:
            page.arrow(x + cardWidth + 0.03, x + cardWidth + gap - 0.03, top - height / 2)
    enrichX = MARGIN + 2 * (cardWidth + gap)
    tailsY = top - height - 0.2
    page.fig.add_artist(
        Line2D(
            [(enrichX + 0.1) / PAGE_WIDTH] * 2,
            [(top - height) / PAGE_HEIGHT, (tailsY + 0.03) / PAGE_HEIGHT],
            color=AXIS,
            linewidth=1.0,
        )
    )
    page.text(
        enrichX + 0.17,
        tailsY,
        f"Depleted tails: {core.tails / 1e3:,.1f} tU at {100 * u.tailsAssay:.3f} wt%",
        size=7,
        color=SECONDARY,
    )

    # where each $/MWh goes
    y = tailsY - 0.42
    page.heading(y, "Where each $/MWh goes", f"levelized over the plant's life, ${lc.lcoe:.2f}/MWh")
    levelized = lc.lcoeByCategory()
    barTop, barHeight = y - 0.35, 0.3
    x = MARGIN
    for cat in fuelCost.COST_CATEGORIES:
        width = CONTENT_WIDTH * levelized[cat] / lc.lcoe
        page.box(x, barTop - barHeight, max(width - 0.025, 0.01), barHeight, color=CATEGORY_COLORS[cat], radius=0.03)
        share = levelized[cat] / lc.lcoe
        if width > 0.55:
            ink = INK if cat == "fabrication" else WHITE
            page.text(x + 0.08, barTop - barHeight / 2 - 0.035, f"{share:.0%}", size=8, weight="bold", color=ink)
        x += width
    legendY = barTop - barHeight - 0.25
    legendWidth = CONTENT_WIDTH / len(fuelCost.COST_CATEGORIES)
    for i, cat in enumerate(fuelCost.COST_CATEGORIES):
        lx = MARGIN + i * legendWidth
        page.box(lx, legendY - 0.01, 0.09, 0.09, color=CATEGORY_COLORS[cat], radius=0.015)
        page.text(lx + 0.15, legendY, SHORT_LABELS[cat], size=7.5, color=SECONDARY)
        page.text(
            lx + 0.15,
            legendY - 0.17,
            f"${levelized[cat]:.2f}  ·  {levelized[cat] / lc.lcoe:.0%}",
            size=8,
            weight="bold",
        )

    # every step
    y = legendY - 0.55
    page.heading(y, "Cost of each step", "initial core, plant life and levelized")
    lifetime = lc.spendByCategory()
    quantity = {
        "ore": f"{core.u3o8Lb / 1e3:,.0f} klb U3O8",
        "conversion": f"{core.feed / 1e3:,.1f} tU",
        "enrichment": f"{core.swu / 1e3:,.1f} tSWU",
        "fabrication": f"{core.heavyMetal / 1e3:,.2f} tHM",
        "backEnd": f"{lc.totalGeneration / 1e6:,.1f} TWh, life",
    }
    columns = [
        ("Step", "left", 1.7),
        ("Quantity,\ninitial core", "right", 1.1),
        ("Unit price", "right", 1.0),
        ("Initial core\n$M", "right", 0.8),
        ("Plant life\n$M", "right", 0.8),
        ("Levelized\n$/MWh", "right", 0.75),
        ("Share", "right", 0.5),
    ]
    rows = [
        [
            fuelCost.CATEGORY_LABELS[cat],
            quantity[cat],
            _unitPrice(cat, core),
            f"{initial[cat] / 1e6:,.2f}" if cat in initial else "–",
            f"{lifetime[cat] / 1e6:,.1f}",
            f"{levelized[cat]:.2f}",
            f"{levelized[cat] / lc.lcoe:.0%}",
        ]
        for cat in fuelCost.COST_CATEGORIES
    ]
    total = ["Total", "", "", f"{core.total / 1e6:,.2f}", f"{lc.totalSpend / 1e6:,.1f}", f"{lc.lcoe:.2f}", "100%"]
    y = page.table(y - 0.12, columns, rows, total, swatches=[CATEGORY_COLORS[c] for c in fuelCost.COST_CATEGORIES])

    # key points
    points = _keyPoints(lc)
    top = y - 0.32
    height = 0.3 + 0.22 * len(points)
    page.box(MARGIN, top - height, CONTENT_WIDTH, height)
    page.box(MARGIN, top - height, 0.05, height, color=ACCENT, rounded=False)
    page.text(MARGIN + 0.22, top - 0.25, "KEY POINTS", size=6.5, color=ACCENT, weight="bold")
    for i, point in enumerate(points):
        page.text(MARGIN + 0.22, top - 0.47 - 0.22 * i, "•  " + point, size=8, color=INK)


def _reloadsPage(page, lc):
    page.pageTitle(
        "Reloads and fuel utilization", "How well the fuel is used, and each cycle's fresh fuel against its electricity"
    )

    # fuel utilization
    y = PAGE_HEIGHT - 1.95
    page.heading(y, "Fuel utilization", "over the plant's life, every load included")
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
    metrics = [
        ("Discharge burnup", f"{lc.dischargeBurnup:,.1f}", "GWd/tHM, life average"),
        ("Specific power", f"{lc.thermalPowerMW / (loads[0].heavyMetal / 1e3):,.1f}", "kW/kgHM, initial core"),
        ("Reload enrichment", f"{100 * reloadAssay:.2f}", f"wt% U-235, feed/product {feedPerProduct:.1f}"),
        ("Fresh fuel", f"${sum(c.total for c in loads) / heavyMetal:,.0f}", "per kgHM, front end"),
        ("Natural uranium", f"{natural / gwh:,.2f}", "kgU per GWh(e)"),
        ("Separative work", f"{swu / gwh:,.2f}", "kg-SWU per GWh(e)"),
        ("Uranium utilization", f"{lc.thermalEnergyMWd / natural:,.2f}", "MWd(th) per kg natural U"),
        ("Fuel throughput", f"{heavyMetal / 1e3 / lc.years:,.2f}", f"tHM/y; {uranium / 1e3:,.1f} tU in all"),
    ]
    page.metricGrid(y - 0.25, metrics, columns=4)
    y -= 2.05

    # fuel bought for each load
    page.heading(y, "Fuel bought for each load", "$M by step, at the load's start; lead times not shown")
    legendY = y - 0.36
    for i, cat in enumerate(fuelCost.FRONT_END):
        lx = MARGIN + i * 1.2
        page.box(lx, legendY - 0.01, 0.09, 0.09, color=CATEGORY_COLORS[cat], radius=0.015)
        page.text(lx + 0.15, legendY, SHORT_LABELS[cat], size=7.5, color=SECONDARY)
    ax = page.axes(MARGIN + 0.5, y - 1.9, CONTENT_WIDTH - 0.5, 1.32)
    loads = lc.costs
    peak = max(c.total for c in loads) / 1e6
    width = min(0.62, 0.9 * 10 / max(len(loads), 10))
    for i, cost in enumerate(loads):
        _stackedBar(ax, i, width, [(CATEGORY_COLORS[c], cost.byCategory()[c] / 1e6) for c in fuelCost.FRONT_END], peak)
        if i and abs(cost.total - loads[i - 1].total) <= 1e-3 * cost.total and len(loads) > 12:
            continue
        ax.annotate(
            f"${cost.total / 1e6:,.1f}M",
            (i, cost.total / 1e6),
            xytext=(0, 3),
            textcoords="offset points",
            ha="center",
            size=6.5,
            color=SECONDARY,
        )
    every = max(1, math.ceil(len(loads) / 12))
    ticks = [i for i in range(len(loads)) if i % every == 0]
    ax.set_xticks(
        ticks,
        [f"{'Initial' if i == 0 else f'Cycle {lc.charges[i].cycle + 1}'}\n{lc.charges[i].time:.1f} y" for i in ticks],
    )
    ax.tick_params(axis="x", labelsize=6.5)
    ax.set_xlim(-0.6, len(loads) - 0.4)
    ax.set_ylim(0, peak * 1.18)
    ax.yaxis.set_major_locator(MaxNLocator(4, steps=[1, 2, 5, 10]))
    ax.yaxis.set_major_formatter(lambda v, _: f"${v:,.0f}M")
    ax.grid(axis="x", visible=False)

    # cycle by cycle
    y -= 2.5
    page.heading(y, "Cycle by cycle", "the fresh fuel loaded for each cycle against the electricity it makes")
    columns = [
        ("Cycle", "left", 0.95),
        ("Starts\nyear", "right", 0.55),
        ("Fresh fuel\ntHM", "right", 0.72),
        ("Natural U\nklb U3O8", "right", 0.78),
        ("Separative\ntSWU", "right", 0.72),
        ("Fuel bought\n$M", "right", 0.8),
        ("Spent fuel\n$M", "right", 0.72),
        ("Electricity\nGWh", "right", 0.8),
        ("Fuel cost\n$/MWh", "right", 0.72),
    ]
    summaries = lc.cycleSummaries()

    def row(group):
        s = group[0]
        loaded = s.loaded
        label = f"{s.cycle + 1}" if len(group) == 1 else f"{s.cycle + 1}–{group[-1].cycle + 1}, {len(group)}× each"
        return [
            label,
            f"{s.start:.1f}",
            f"{loaded.heavyMetal / 1e3:,.2f}" if loaded else "–",
            f"{loaded.u3o8Lb / 1e3:,.0f}" if loaded else "–",
            f"{loaded.swu / 1e3:,.1f}" if loaded else "–",
            f"{s.frontEnd / 1e6:,.2f}",
            f"{s.backEnd / 1e6:,.2f}",
            f"{s.generation / 1e3:,.0f}",
            f"{s.costPerMWh:.2f}",
        ]

    groups = _groupAlike(summaries)
    shown = len(groups) if len(groups) <= MAX_CYCLE_ROWS else MAX_CYCLE_ROWS - 1
    rows = [row(g) for g in groups[:shown]]
    if shown < len(groups):
        rest = [s for g in groups[shown:] for s in g]
        rows.append(
            [
                f"{rest[0].cycle + 1}–{rest[-1].cycle + 1}, all",
                f"{rest[0].start:.1f}",
                "",
                "",
                "",
                f"{sum(s.frontEnd for s in rest) / 1e6:,.2f}",
                f"{sum(s.backEnd for s in rest) / 1e6:,.2f}",
                f"{sum(s.generation for s in rest) / 1e3:,.0f}",
                f"{sum(s.total for s in rest) / sum(s.generation for s in rest):.2f}",
            ]
        )
    loads = lc.costs
    total = [
        "Total",
        "",
        f"{sum(c.heavyMetal for c in loads) / 1e3:,.2f}",
        f"{sum(c.u3o8Lb for c in loads) / 1e3:,.0f}",
        f"{sum(c.swu for c in loads) / 1e3:,.1f}",
        f"{sum(c.total for c in loads) / 1e6:,.2f}",
        f"{sum(s.backEnd for s in summaries) / 1e6:,.2f}",
        f"{lc.totalGeneration / 1e3:,.0f}",
        f"{lc.undiscountedLcoe:.2f}",
    ]
    y = page.table(y - 0.12, columns, rows, total)
    page.note(y - 0.2, f"Reloads: {_reloadsText(lc)}. Fuel still in the core after the last cycle is not credited.")
    page.note(
        y - 0.37,
        "Fuel cost: the fuel bought for a cycle plus its spent fuel fees, over that cycle's electricity, "
        "undiscounted; the total row is the plant-life average.",
    )


def _cashFlowPage(page, lc):
    page.pageTitle("Cash flow and sensitivity", "When the money goes out, and what moves the fuel cycle cost")

    # cumulative spend
    y = PAGE_HEIGHT - 1.95
    page.heading(
        y, "Cumulative fuel spend", f"$M, undiscounted, as paid; {_money(lc.totalSpend)} over the plant's life"
    )
    legendY = y - 0.36
    for i, cat in enumerate(fuelCost.COST_CATEGORIES):
        lx = MARGIN + i * 1.2
        page.box(lx, legendY - 0.01, 0.09, 0.09, color=CATEGORY_COLORS[cat], radius=0.015)
        page.text(lx + 0.15, legendY, SHORT_LABELS[cat], size=7.5, color=SECONDARY)
    ax = page.axes(MARGIN + 0.5, y - 2.25, CONTENT_WIDTH - 0.5, 1.65)
    flows = lc.purchases
    t0 = min(year for year, _ in flows)
    times = np.linspace(t0, lc.years, 600)
    lower = np.zeros_like(times)
    for cat in fuelCost.COST_CATEGORIES:
        if cat == "backEnd":
            rate = lc.unitCosts.backEndPerMWh * lc.electricPowerMW * 24 * 365.25 / 1e6
            paid = np.array(
                [
                    sum(rate * c.capacityFactor * min(max(tt - c.start, 0.0), c.length) for c in lc.cycles)
                    for tt in times
                ]
            )
        else:
            cash = lc.cashFlows(cat)
            paid = np.array([sum(a for yr, a in cash if yr <= tt) for tt in times]) / 1e6
        upper = lower + paid
        ax.fill_between(times, lower, upper, color=CATEGORY_COLORS[cat], linewidth=0, step=None)
        lower = upper
    ax.plot(times, lower, color=INK, linewidth=1.0)
    ax.axvline(0, color=INK, linewidth=0.8)
    ax.annotate(
        "first startup",
        (0, lower.max() * 1.05),
        xytext=(4, 0),
        textcoords="offset points",
        size=7,
        color=SECONDARY,
        va="center",
    )
    ax.set_xlim(t0, lc.years)
    ax.set_ylim(0, lower.max() * 1.12)
    ax.yaxis.set_major_locator(MaxNLocator(5, steps=[1, 2, 5, 10]))
    ax.yaxis.set_major_formatter(lambda v, _: f"${v:,.0f}M")
    ax.set_xlabel("Years from first startup", size=7.5)
    ax.grid(axis="x", visible=False)

    y -= 2.95
    page.heading(y, "What moves the fuel cycle cost", f"$/MWh levelized, base ${lc.lcoe:.2f}")
    rows = lc.sensitivities()
    height = 0.28 * len(rows) + 0.3
    labelWidth = 1.75
    ax = page.axes(MARGIN + labelWidth, y - 0.3 - height, CONTENT_WIDTH - labelWidth, height)
    base = lc.lcoe
    span = max(max(abs(r[1] - base), abs(r[2] - base)) for r in rows) or 1.0
    for i, (_, low, high, lowLabel, highLabel) in enumerate(rows):
        for value, label in ((low, lowLabel), (high, highLabel)):
            if abs(value - base) < 1e-9:
                continue
            ax.barh(i, value - base, left=base, height=0.5, color=LOWER if value < base else HIGHER, linewidth=0)
            ax.annotate(
                f"{label}   ${value:.2f}",
                (value, i),
                xytext=(5 if value >= base else -5, 0),
                textcoords="offset points",
                ha="left" if value >= base else "right",
                va="center",
                size=7,
                color=SECONDARY,
            )
    ax.axvline(base, color=INK, linewidth=0.9)
    ax.set_yticks(range(len(rows)), [r[0] for r in rows])
    ax.tick_params(axis="y", labelcolor=INK, labelsize=8, pad=10)
    ax.invert_yaxis()
    ax.set_ylim(len(rows) - 0.4, -0.6)
    ax.set_xlim(base - 1.7 * span, base + 1.7 * span)
    ax.grid(axis="y", visible=False)
    ax.xaxis.set_major_locator(MaxNLocator(7, steps=[1, 2, 5, 10]))
    ax.xaxis.set_major_formatter(lambda v, _: f"${v:,.0f}")
    page.note(
        y - 0.55 - height,
        "Prices move ±50%. The discount rate moves 4 points down and 3 up; the capacity factor 15 points down "
        "and 5 up,",
    )
    page.note(y - 0.71 - height, "each cycle then making the same energy over a different calendar time.")
    for i, (color, label) in enumerate(((LOWER, "lowers the cost"), (HIGHER, "raises the cost"))):
        lx = RIGHT - 2.3 + i * 1.2
        page.box(lx, y - 0.33, 0.09, 0.09, color=color, radius=0.015)
        page.text(lx + 0.15, y - 0.32, label, size=7, color=SECONDARY)

    # from undiscounted to levelized
    y -= 1.2 + height
    page.heading(y, "From fuel bought to levelized cost", "$/MWh: what the time value of money adds")
    noLead = lc.repriced(replace(lc.unitCosts, leadTimes={})).lcoe
    steps = [
        ("Undiscounted", 0.0, lc.undiscountedLcoe, MUTED, "every dollar paid over every MWh made"),
        (
            "Discounting",
            lc.undiscountedLcoe,
            noLead,
            "#6f9ad8",
            f"fuel is paid before its electricity, at {lc.discountRate:.0%}",
        ),
        ("Paying ahead of loading", noLead, lc.lcoe, "#9dbbe6", "each step paid its lead time before loading"),
        ("Levelized fuel cycle cost", 0.0, lc.lcoe, ACCENT, ""),
    ]
    ax = page.axes(MARGIN + 1.75, y - 1.55, CONTENT_WIDTH - 1.75, 1.25)
    for i, (name, start, end, color, note) in enumerate(steps):
        ax.barh(i, end - start, left=start, height=0.52, color=color, linewidth=0)
        change = end - start
        text = f"${end:.2f}" if start == 0.0 else f"+${change:.2f}"
        ax.annotate(
            f"{text}   {note}".rstrip(),
            (end, i),
            xytext=(5, 0),
            textcoords="offset points",
            va="center",
            size=7,
            color=SECONDARY,
        )
        if 0 < i < len(steps) - 1:
            ax.plot([start, start], [i - 1 + 0.26, i - 0.26], color=AXIS, linewidth=0.6)
    ax.set_yticks(range(len(steps)), [s[0] for s in steps])
    ax.tick_params(axis="y", labelcolor=INK, labelsize=8, pad=10)
    ax.invert_yaxis()
    ax.set_xlim(0, lc.lcoe * 1.75)
    ax.grid(axis="y", visible=False)
    ax.xaxis.set_major_locator(MaxNLocator(6, steps=[1, 2, 5, 10]))
    ax.xaxis.set_major_formatter(lambda v, _: f"${v:,.0f}")


def _basisPage(page, lc):
    page.pageTitle("Basis and method", "The initial core by fuel group, every assumption, and how the numbers are made")

    # initial core by fuel group
    y = PAGE_HEIGHT - 1.95
    core = lc.fuelCost
    page.heading(y, "Initial core by fuel group", "each fuel form and enrichment")
    columns = [
        ("Fuel group", "left", 1.3),
        ("Heavy metal\nkg", "right", 0.8),
        ("Natural U\nkg", "right", 0.8),
        ("Separative\nkg-SWU", "right", 0.8),
        ("Natural U\n$M", "right", 0.7),
        ("Conversion\n$M", "right", 0.72),
        ("Enrichment\n$M", "right", 0.72),
        ("Fabrication\n$M", "right", 0.72),
        ("Total\n$M", "right", 0.6),
    ]
    rows = [
        [
            f"{g.group.form}, {100 * g.group.assay:.2f} wt%",
            f"{g.group.heavyMetal:,.0f}",
            f"{g.oreUranium:,.0f}",
            f"{g.requirement.swu:,.0f}",
            *(f"{g.costs[cat] / 1e6:,.2f}" for cat in fuelCost.FRONT_END),
            f"{g.total / 1e6:,.2f}",
        ]
        for g in core.groups
    ]
    categories = core.byCategory()
    total = [
        "Total",
        f"{core.heavyMetal:,.0f}",
        f"{core.oreUranium:,.0f}",
        f"{core.swu:,.0f}",
        *(f"{categories[cat] / 1e6:,.2f}" for cat in fuelCost.FRONT_END),
        f"{core.total / 1e6:,.2f}",
    ]
    y = page.table(y - 0.12, columns, rows, total)

    # basis
    y -= 0.55
    columnWidth = (CONTENT_WIDTH - 0.4) / 2
    cycle = lc.cycles[0]
    uniform = all(abs(c.length - cycle.length) < 1e-9 for c in lc.cycles)
    operation = [
        ("Thermal power", f"{lc.thermalPowerMW:,.1f} MWt"),
        ("Net thermal efficiency", f"{lc.thermalEfficiency:.1%}"),
        ("Net electric power", f"{lc.electricPowerMW:,.1f} MWe"),
        ("Capacity factor", f"{lc.capacityFactor:.1%}"),
        (
            "Cycles",
            f"{len(lc.cycles)} × {cycle.length:.2f} y, {cycle.length * cycle.capacityFactor:.2f} EFPY each"
            if uniform
            else f"{len(lc.cycles)}, {lc.years / len(lc.cycles):.2f} y average",
        ),
        ("Operating life", f"{lc.years:.1f} y, {lc.fullPowerYears:.1f} EFPY"),
        ("Reloads", "from the run" if lc.source == "run" else _projectedText(lc)),
        ("Discount rate", f"{lc.discountRate:.1%} real"),
    ]
    u = lc.unitCosts
    forms = sorted({g.group.form for c in lc.costs for g in c.groups})
    prices = [
        ("Natural uranium", f"${u.orePerLbU3O8:.0f}/lb U3O8  (${u.orePerKgU:.0f}/kgU)"),
        ("Conversion", f"${u.conversionPerKgU:.0f}/kgU, {u.conversionLoss:.1%} loss"),
        ("Enrichment", f"${u.swu:.0f}/SWU"),
        ("Feed / tails assay", f"{100 * u.feedAssay:.3f} / {100 * u.tailsAssay:.3f} wt%"),
        *((f"Fabrication, {form}", f"${u.fabricationPerKgHM[form]:,.0f}/kgHM") for form in forms),
        ("Spent fuel", f"${u.backEndPerMWh:.2f}/MWh"),
        ("Paid before loading", "  ·  ".join(f"{LEAD_LABELS[c]} {u.leadTime(c):g}" for c in fuelCost.FRONT_END) + " y"),
    ]
    page.keyValues(MARGIN, y, columnWidth, "Plant and operation", operation)
    page.keyValues(MARGIN + columnWidth + 0.4, y, columnWidth, "Prices and assumptions", prices)

    # method
    y -= 0.33 + ROW * max(len(operation), len(prices)) + 0.5
    page.heading(y, "Method", "U-235 mass fractions x; masses in kg of uranium")
    equations = [
        (
            "Feed",
            r"$F\,/\,P \;=\; (x_p - x_t)\;/\;(x_f - x_t)$",
            "feed F and product P; natural U bought = F / (1 - conversion loss)",
        ),
        (
            "Separative work",
            r"$S\,/\,P \;=\; V(x_p) + (T/P)\,V(x_t) - (F/P)\,V(x_f), \qquad V(x) = (1 - 2x)\,\ln[(1-x)/x]$",
            "tails T = F - P; product below natural assay is blended down and takes no separative work",
        ),
        (
            "Levelized cost",
            r"$\mathrm{LCOE} \;=\; \Sigma_i\; C_i\,(1+r)^{-t_i} \;\;/\;\; \Sigma_j\; E_j\,(1+r)^{-t_j}$",
            "each step paid its lead time before its load; electricity credited at each cycle's midpoint",
        ),
        (
            "Discharge burnup",
            r"$\bar{B} \;=\; \Sigma_c\; P_{th}\, f_c\, T_c \;\;/\;\; \Sigma_k\; M_{HM,k}$",
            "thermal energy over all heavy metal loaded; capacity factor f, cycle length T",
        ),
    ]
    for i, (name, formula, note) in enumerate(equations):
        rowY = y - 0.45 - 0.58 * i
        page.text(MARGIN, rowY, name, size=8, color=SECONDARY, weight="bold")
        page.text(MARGIN + 1.35, rowY, formula, size=11, parse_math=True)
        page.text(MARGIN + 1.35, rowY - 0.2, note, size=7, color=MUTED)


def _stackedBar(ax, x, width, segments, scale):
    """A vertical bar of ``(color, value)`` segments with a thin surface gap between them."""
    bottom, gap = 0.0, scale * 0.008
    for color, value in segments:
        if value <= 0:
            continue
        ax.add_patch(
            Rectangle((x - width / 2, bottom), width, max(value - gap, value * 0.5), facecolor=color, edgecolor="none")
        )
        bottom += value


def _keyPoints(lc):
    """Three or four computed sentences a decision maker should take away."""
    levelized = lc.lcoeByCategory()
    biggest = max(levelized, key=levelized.get)
    points = [
        f"{fuelCost.CATEGORY_LABELS[biggest]} is the largest part of the fuel cycle cost: "
        f"${levelized[biggest]:.2f} of every ${lc.lcoe:.2f}/MWh ({levelized[biggest] / lc.lcoe:.0%})."
    ]
    if lc.reloads:
        costs = lc.costs[1:]
        mean = sum(c.total for c in costs) / len(costs)
        interval = lc.years / len(lc.cycles)
        what = "measured from the run" if lc.source == "run" else f"{lc.reloadFraction:.0%} of the core"
        points.append(
            f"After the {_money(lc.costs[0].total)} initial core, {len(costs)} reloads ({what}) average {_money(mean)} "
            f"each, about every {interval:.1f} years."
        )
    name, low, high, lowLabel, highLabel = lc.sensitivities()[0]
    points.append(
        f"The biggest lever is {_leverName(name)}: {lowLabel} to {highLabel} moves the cost from ${low:.2f} "
        f"to ${high:.2f}/MWh."
    )
    noLead = lc.repriced(replace(lc.unitCosts, leadTimes={})).lcoe
    points.append(
        f"Buying fuel ahead of loading adds ${lc.lcoe - noLead:.2f}/MWh at a {lc.discountRate:.0%} discount rate; "
        f"undiscounted, the fuel costs ${lc.undiscountedLcoe:.2f}/MWh."
    )
    return points


def _groupAlike(summaries):
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


def _leverName(name):
    """A sensitivity's input as it reads mid-sentence."""
    if name in fuelCost.CATEGORY_LABELS.values():
        return f"the {name[0].lower()}{name[1:]} price"
    return f"the {name.lower()}"


def _basisLine(lc):
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


def _unitPrice(category, core):
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


def _projectedText(lc):
    return f"projected, {lc.reloadFraction:.0%} of the core" if lc.reloads else "none"


def _reloadsText(lc):
    if lc.source == "run":
        return f"{len(lc.reloads)} batches of fresh fuel, as loaded by the run's fuel management"
    if not lc.reloads:
        return "none (one core for the whole life)"
    return f"projected as {lc.reloadFraction:.0%} of the initial core at the start of each later cycle"


def _money(dollars):
    return f"${dollars / 1e9:,.2f}B" if dollars >= 1e9 else f"${dollars / 1e6:,.1f}M"
