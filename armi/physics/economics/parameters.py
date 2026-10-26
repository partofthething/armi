"""Core parameters for the economics plugin."""

from armi.reactor import parameters
from armi.reactor.parameters import ParamLocation
from armi.reactor.reactors import Core
from armi.utils import units


def getEconomicsParameterDefinitions():
    """Return the economics parameter definitions for each part of the reactor model."""
    pDefs = parameters.ParameterDefinitionCollection()

    with pDefs.createBuilder(default=0.0, location=ParamLocation.AVERAGE, categories=["economics"]) as pb:
        pb.defParam(
            "fuelCostInitialCore",
            units=units.USD,
            description="Cost of the initial core's fresh fuel: ore, conversion, enrichment and fabrication",
        )

        pb.defParam(
            "fuelCostLcoe",
            units=f"{units.USD}/MWh",
            description="Levelized fuel cost of electricity over the plant's cycles",
        )

    return {Core: pDefs}
