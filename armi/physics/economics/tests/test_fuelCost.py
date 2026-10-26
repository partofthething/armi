"""Tests for the fuel cost of a core, its interface and its report."""

import os
import unittest
from types import SimpleNamespace

from armi.physics.economics import economicsInterface, enrichment, fuelCost, report
from armi.physics.economics import settings as econSettings
from armi.testing import loadTestReactor
from armi.utils import directoryChangers


class TestFuelCost(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.o, cls.r = loadTestReactor(
            customSettings={econSettings.CONF_ECON_FUEL_COST: True, "nCycles": 4, "cycleLength": 400.0}
        )
        cls.cs = cls.o.cs

    def test_inventory(self):
        """Every kg of heavy metal in the core lands in a group, scaled to the full core."""
        groups = fuelCost.fuelInventory(self.r.core)
        self.assertEqual({g.form for g in groups}, {"U-metal"})
        totalHM = sum(b.getHMMass() for b in self.r.core.iterBlocks()) * self.r.core.powerMultiplier / 1000.0
        self.assertAlmostEqual(sum(g.heavyMetal for g in groups), totalHM, places=6)
        self.assertEqual(len({round(g.assay, 5) for g in groups}), len(groups))

    def test_formOverride(self):
        groups = fuelCost.fuelInventory(self.r.core, "TRISO")
        self.assertEqual({g.form for g in groups}, {"TRISO"})

    def test_groupCost(self):
        unitCosts = fuelCost.UnitCosts.fromSettings(self.cs)
        group = fuelCost.FuelGroup("UO2", heavyMetal=1000.0, uranium=1000.0, u235=45.0)
        cost = fuelCost.costFuel([group], unitCosts).groups[0]
        req = enrichment.enrich(1000.0, 0.045)
        self.assertAlmostEqual(cost.costs["enrichment"], req.swu * self.cs[econSettings.CONF_ECON_SWU_COST])
        self.assertAlmostEqual(cost.costs["fabrication"], 1000.0 * self.cs[econSettings.CONF_ECON_FAB_COST_UO2])
        self.assertAlmostEqual(cost.oreUranium, req.feed / (1 - self.cs[econSettings.CONF_ECON_CONVERSION_LOSS]))

    def test_optimizedTails(self):
        cs = self.cs.modified(newSettings={econSettings.CONF_ECON_OPTIMIZE_TAILS: True})
        unitCosts = fuelCost.UnitCosts.fromSettings(cs)
        self.assertNotAlmostEqual(unitCosts.tailsAssay, self.cs[econSettings.CONF_ECON_TAILS_ASSAY])

    def test_levelizedCost(self):
        levelized = economicsInterface.costCore(self.r.core, self.cs)
        purchases = levelized.purchases
        self.assertEqual(len(purchases), 4)
        self.assertAlmostEqual(purchases[0][1], levelized.fuelCost.total)
        energy = sum(mwh for _, mwh in levelized.generation)
        self.assertAlmostEqual(levelized.undiscountedLcoe, 4 * levelized.fuelCost.total / energy)
        self.assertGreater(levelized.lcoe, levelized.undiscountedLcoe)
        self.assertIn("Overnight cost", economicsInterface.summarize(levelized))

    def test_interfaceAndReport(self):
        interface = self.o.getInterface("economics")
        self.assertIsNotNone(interface)
        with directoryChangers.TemporaryDirectoryChanger():
            interface.cs = self.cs.modified(newSettings={econSettings.CONF_ECON_FUEL_COST_REPORT: True})
            interface.interactInit()
            self.assertGreater(self.r.core.p.fuelCostInitialCore, 0.0)
            self.assertGreater(self.r.core.p.fuelCostLcoe, 0.0)
            self.assertTrue(os.path.exists(f"{self.cs.caseTitle}-fuel-cost.pdf"))

    def test_reportPath(self):
        with directoryChangers.TemporaryDirectoryChanger():
            levelized = economicsInterface.costCore(self.r.core, self.cs)
            self.assertEqual(report.writeFuelCostReport(levelized, "cost.pdf"), "cost.pdf")

    def test_offByDefault(self):
        cs = self.cs.modified(newSettings={econSettings.CONF_ECON_FUEL_COST: False})
        self.assertIsNone(economicsInterface.describeInterfaces(cs))


class TestClassifyFuelForm(unittest.TestCase):
    @staticmethod
    def _component(masses, blockMasses=None):
        """A stand-in component whose masses (g) come from dicts keyed by element."""

        def mass(table):
            return lambda element: table.get(element, 0.0)

        block = None
        if blockMasses is not None:
            block = SimpleNamespace(getMass=mass(blockMasses), getHMMass=lambda: blockMasses.get("U", 0.0))
        return SimpleNamespace(parent=block, getMass=mass(masses), getHMMass=lambda: masses.get("U", 0.0))

    def test_forms(self):
        oxide = {"U": 88.0, "O": 12.0}
        self.assertEqual(fuelCost.classifyFuelForm(self._component(oxide)), "UO2")
        self.assertEqual(fuelCost.classifyFuelForm(self._component({"U": 90.0, "ZR": 10.0})), "U-metal")
        compact = {"U": 10.0, "O": 1.2, "C": 80.0, "SI": 5.0}
        self.assertEqual(fuelCost.classifyFuelForm(self._component(oxide, compact)), "TRISO")

    def test_absorberCarbonIsNotTriso(self):
        """An LWR assembly with steel (Si) and B4C (C) is still UO2."""
        assembly = {"U": 1000.0, "O": 134.0, "C": 0.2, "SI": 0.04}
        self.assertEqual(fuelCost.classifyFuelForm(self._component({"U": 88.0, "O": 12.0}, assembly)), "UO2")
