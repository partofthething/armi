"""
Settings for the economics plugin.

Default prices are rough mid-2020s market and literature values in US dollars, good for a first estimate and meant
to be overridden. Fabrication costs are per kg of heavy metal and cover the whole fuel form, e.g. pellets, rods and
assembly hardware for UO2, or kernels, coatings and compacts for TRISO.
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

#: Fuel forms with their own fabrication cost, and the setting that holds it.
FUEL_FORMS = {
    "UO2": CONF_ECON_FAB_COST_UO2,
    "U-metal": CONF_ECON_FAB_COST_UMETAL,
    "TRISO": CONF_ECON_FAB_COST_TRISO,
}


def defineSettings():
    """Define the economics settings."""
    return [
        setting.Setting(
            CONF_ECON_FUEL_COST,
            default=False,
            label="Fuel cost",
            description="Compute the cost of the fresh core's fuel and its levelized cost of electricity at startup.",
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
            description="Fraction of the initial core's fuel bought again at the start of every later cycle: 1 for "
            "whole-core refueling, about 1/n for an n-batch core.",
        ),
    ]


def defineValidators(inspector):
    """Check that the economics settings are physical."""
    cs = inspector.cs

    def isFraction(name, allowZero, allowOne):
        value = cs[name]
        return (value >= 0.0 if allowZero else value > 0.0) and (value <= 1.0 if allowOne else value < 1.0)

    return [
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
