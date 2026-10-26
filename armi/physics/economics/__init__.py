"""
Generic economics plugin: what a reactor's fuel costs, and later its capital.

At startup it costs the fresh core's fuel from ore, conversion, enrichment and fabrication prices, levelizes it over
the plant's cycles, and optionally writes a PDF report. Turn it on with the ``econFuelCost`` setting.
"""

from armi import plugins


class EconomicsPlugin(plugins.ArmiPlugin):
    """Plugin for fuel cycle and plant economics."""

    @staticmethod
    @plugins.HOOKIMPL
    def exposeInterfaces(cs):
        """Expose the economics interface."""
        from armi.physics.economics import economicsInterface

        return plugins.collectInterfaceDescriptions(economicsInterface, cs)

    @staticmethod
    @plugins.HOOKIMPL
    def defineSettings():
        """Define settings for economics."""
        from armi.physics.economics import settings

        return settings.defineSettings()

    @staticmethod
    @plugins.HOOKIMPL
    def defineSettingsValidators(inspector):
        """Check the economics settings."""
        from armi.physics.economics import settings

        return settings.defineValidators(inspector)

    @staticmethod
    @plugins.HOOKIMPL
    def defineParameters():
        """Define the economics parameters on the core."""
        from armi.physics.economics import parameters

        return parameters.getEconomicsParameterDefinitions()
