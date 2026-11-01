"""
Settings for the economics plugin.

Default prices are rough mid-2020s market and literature values in US dollars, good for a first estimate and meant
to be overridden. Fabrication costs are per kg of heavy metal and cover the whole fuel form, e.g. pellets, rods and
assembly hardware for UO2, or kernels, coatings and compacts for TRISO. The spent fuel fee defaults to the US Nuclear
Waste Policy Act's 1 mill/kWh, and the lead times to the usual front-end schedule (ore bought about two years before
the fuel is loaded, fabrication paid about half a year before).
"""

from armi.settings import setting
from armi.settings.settingsValidation import Query

CONF_ECON_FUEL_COST = "econFuelCost"
CONF_ECON_FUEL_COST_REPORT = "econFuelCostReport"
CONF_ECON_FUEL_FORM = "econFuelForm"
CONF_ECON_ORE_PRICE = "econOrePrice"
CONF_ECON_CONVERSION_COST = "econConversionCost"
CONF_ECON_CONVERSION_LOSS = "econConversionLoss"
CONF_ECON_SWU_COST = "econSwuCost"
CONF_ECON_FEED_ASSAY = "econFeedAssay"
CONF_ECON_TAILS_ASSAY = "econTailsAssay"
CONF_ECON_OPTIMIZE_TAILS = "econOptimizeTails"
CONF_ECON_FAB_COST_UO2 = "econFabCostUO2"
CONF_ECON_FAB_COST_UMETAL = "econFabCostUMetal"
CONF_ECON_FAB_COST_TRISO = "econFabCostTRISO"
CONF_ECON_THERMAL_EFFICIENCY = "econThermalEfficiency"
CONF_ECON_DISCOUNT_RATE = "econDiscountRate"
CONF_ECON_RELOAD_FRACTION = "econReloadFraction"
CONF_ECON_RELOADS = "econReloads"
CONF_ECON_BACK_END_COST = "econBackEndCost"
CONF_ECON_LEAD_ORE = "econLeadTimeOre"
CONF_ECON_LEAD_CONVERSION = "econLeadTimeConversion"
CONF_ECON_LEAD_ENRICHMENT = "econLeadTimeEnrichment"
CONF_ECON_LEAD_FABRICATION = "econLeadTimeFabrication"

#: Fuel forms with their own fabrication cost, and the setting that holds it.
FUEL_FORMS = {
    "UO2": CONF_ECON_FAB_COST_UO2,
    "U-metal": CONF_ECON_FAB_COST_UMETAL,
    "TRISO": CONF_ECON_FAB_COST_TRISO,
}


#: Front-end cost categories and the setting that holds how long before loading each is paid.
LEAD_TIMES = {
    "ore": CONF_ECON_LEAD_ORE,
    "conversion": CONF_ECON_LEAD_CONVERSION,
    "enrichment": CONF_ECON_LEAD_ENRICHMENT,
    "fabrication": CONF_ECON_LEAD_FABRICATION,
}

#: Where the reloads come from.
RELOAD_SOURCES = ("auto", "run", "projected")


def defineSettings():
    """Define the economics settings."""
    return [
        setting.Setting(
            CONF_ECON_FUEL_COST,
            default=False,
            label="Fuel cost",
            description=(
                "Cost the fuel cycle: the initial core at startup, every reload, and spent fuel, levelized against the "
                "electricity the plant makes."
            ),
        ),
        setting.Setting(
            CONF_ECON_FUEL_COST_REPORT,
            default=False,
            label="Fuel cost report",
            description="Also write the fuel cost breakdown to a PDF report, `<caseTitle>-fuel-cost.pdf`.",
        ),
        setting.Setting(
            CONF_ECON_FUEL_FORM,
            default="auto",
            label="Fuel form",
            description=(
                "Fuel form that sets the fabrication cost of every fuel component. `auto` decides per component: "
                "TRISO if its block holds silicon and at least as much carbon as heavy metal (SiC coatings in "
                "graphite), UO2 if the component is an oxide, U-metal otherwise."
            ),
            options=["auto", *FUEL_FORMS],
        ),
        setting.Setting(
            CONF_ECON_ORE_PRICE,
            default=80.0,
            label="Uranium ore price ($/lb U3O8)",
            description="Price of natural uranium as U3O8, in dollars per pound of U3O8.",
        ),
        setting.Setting(
            CONF_ECON_CONVERSION_COST,
            default=60.0,
            label="Conversion cost ($/kgU)",
            description="Price of converting U3O8 to UF6, in dollars per kg of uranium.",
        ),
        setting.Setting(
            CONF_ECON_CONVERSION_LOSS,
            default=0.005,
            label="Conversion loss",
            description="Fraction of the uranium lost in conversion to UF6.",
        ),
        setting.Setting(
            CONF_ECON_SWU_COST,
            default=190.0,
            label="Separative work cost ($/SWU)",
            description="Price of enrichment, in dollars per kg separative work unit.",
        ),
        setting.Setting(
            CONF_ECON_FEED_ASSAY,
            default=0.00711,
            label="Feed assay",
            description="U-235 mass fraction of the enrichment plant's feed (natural uranium).",
        ),
        setting.Setting(
            CONF_ECON_TAILS_ASSAY,
            default=0.0025,
            label="Tails assay",
            description="U-235 mass fraction left in the enrichment tails.",
        ),
        setting.Setting(
            CONF_ECON_OPTIMIZE_TAILS,
            default=False,
            label="Optimize tails assay",
            description="Use the tails assay that makes enriched product cheapest at these prices instead of "
            f"`{CONF_ECON_TAILS_ASSAY}`.",
        ),
        setting.Setting(
            CONF_ECON_FAB_COST_UO2,
            default=400.0,
            label="UO2 fabrication cost ($/kgHM)",
            description="Fabrication cost of UO2 pellet fuel in rods and assemblies, per kg of heavy metal.",
        ),
        setting.Setting(
            CONF_ECON_FAB_COST_UMETAL,
            default=1500.0,
            label="U-metal fabrication cost ($/kgHM)",
            description="Fabrication cost of metallic uranium (alloy) fuel, per kg of heavy metal.",
        ),
        setting.Setting(
            CONF_ECON_FAB_COST_TRISO,
            default=10000.0,
            label="TRISO fabrication cost ($/kgHM)",
            description="Fabrication cost of TRISO particle fuel in compacts or pebbles, per kg of heavy metal.",
        ),
        setting.Setting(
            CONF_ECON_THERMAL_EFFICIENCY,
            default=0.33,
            label="Net thermal efficiency",
            description="Net electric power over thermal power, to turn `power` into electricity.",
        ),
        setting.Setting(
            CONF_ECON_DISCOUNT_RATE,
            default=0.07,
            label="Discount rate",
            description="Annual real discount rate for levelized costs.",
        ),
        setting.Setting(
            CONF_ECON_RELOAD_FRACTION,
            default=1.0,
            label="Reload fraction",
            description="Fraction of the initial core's fuel bought again at the start of every later cycle when the "
            "reloads are projected: 1 for whole-core refueling, about 1/n for an n-batch core.",
        ),
        setting.Setting(
            CONF_ECON_RELOADS,
            default="auto",
            label="Reloads",
            description=(
                "Where the reloads come from. `run` costs the fresh assemblies the run's fuel management loads at "
                "the start of each cycle and reports at the end of the run. `projected` buys "
                f"`{CONF_ECON_RELOAD_FRACTION}` of the initial core every cycle and reports at startup. `auto` uses "
                "`run` when a fuel handler is set and the run has more than one cycle, `projected` otherwise."
            ),
            options=list(RELOAD_SOURCES),
        ),
        setting.Setting(
            CONF_ECON_BACK_END_COST,
            default=1.0,
            label="Spent fuel cost ($/MWh)",
            description="Spent fuel storage and disposal, as a fee on net generation, in dollars per MWh.",
        ),
        setting.Setting(
            CONF_ECON_LEAD_ORE,
            default=2.0,
            label="Ore lead time (y)",
            description="Years before the fuel is loaded that the natural uranium is paid for.",
        ),
        setting.Setting(
            CONF_ECON_LEAD_CONVERSION,
            default=1.5,
            label="Conversion lead time (y)",
            description="Years before the fuel is loaded that conversion to UF6 is paid for.",
        ),
        setting.Setting(
            CONF_ECON_LEAD_ENRICHMENT,
            default=1.0,
            label="Enrichment lead time (y)",
            description="Years before the fuel is loaded that enrichment is paid for.",
        ),
        setting.Setting(
            CONF_ECON_LEAD_FABRICATION,
            default=0.5,
            label="Fabrication lead time (y)",
            description="Years before the fuel is loaded that fabrication is paid for.",
        ),
    ]


def defineValidators(inspector):
    """Check that the economics settings are physical."""
    cs = inspector.cs

    def isFraction(name, allowZero, allowOne):
        value = cs[name]
        return (value >= 0.0 if allowZero else value > 0.0) and (value <= 1.0 if allowOne else value < 1.0)

    return [
        *(
            Query(
                lambda name=name: cs[name] < 0.0,
                f"`{name}` must not be negative.",
                "",
                inspector.NO_ACTION,
            )
            for name in (CONF_ECON_BACK_END_COST, *LEAD_TIMES.values())
        ),
        Query(
            lambda: not cs[CONF_ECON_TAILS_ASSAY] < cs[CONF_ECON_FEED_ASSAY],
            f"`{CONF_ECON_TAILS_ASSAY}` must be below `{CONF_ECON_FEED_ASSAY}`.",
            "",
            inspector.NO_ACTION,
        ),
        *(
            Query(
                lambda name=name, allowZero=allowZero, allowOne=allowOne: not isFraction(name, allowZero, allowOne),
                f"`{name}` must be a fraction between 0 and 1.",
                "",
                inspector.NO_ACTION,
            )
            for name, allowZero, allowOne in (
                (CONF_ECON_FEED_ASSAY, False, False),
                (CONF_ECON_TAILS_ASSAY, False, False),
                (CONF_ECON_CONVERSION_LOSS, True, False),
                (CONF_ECON_THERMAL_EFFICIENCY, False, True),
                (CONF_ECON_RELOAD_FRACTION, True, True),
            )
        ),
    ]
