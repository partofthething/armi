"""Tests for the fuel cost of a core, its interface and its report."""

import dataclasses
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
        lc = economicsInterface.costCore(self.r.core, self.cs)
        self.assertEqual(len(lc.charges), 4)
        self.assertEqual(lc.source, "projected")
        initialCore = lc.fuelCost
        self.assertAlmostEqual(lc.costs[0].total, initialCore.total)

        spend = lc.spendByCategory()
        backEnd = lc.totalGeneration * self.cs[econSettings.CONF_ECON_BACK_END_COST]
        self.assertAlmostEqual(spend["backEnd"], backEnd)
        self.assertAlmostEqual(lc.totalSpend, 4 * initialCore.total + backEnd)
        self.assertAlmostEqual(lc.undiscountedLcoe, lc.totalSpend / lc.totalGeneration)
        self.assertGreater(lc.lcoe, lc.undiscountedLcoe)
        self.assertAlmostEqual(sum(lc.lcoeByCategory().values()), lc.lcoe)
        self.assertIn("Fuel cycle cost", economicsInterface.summarize(lc))
        self.assertIn("/SWU", lc.unitCosts.describe())

        # ore is paid its lead time before the initial core is loaded
        oreYear = lc.cashFlows("ore")[0][0]
        self.assertAlmostEqual(oreYear, -self.cs[econSettings.CONF_ECON_LEAD_ORE])

    def test_leadTimesCostMoney(self):
        """Paying for fuel earlier raises its levelized cost, and does not change what is paid."""
        lc = economicsInterface.costCore(self.r.core, self.cs)
        noLead = lc.repriced(dataclasses.replace(lc.unitCosts, leadTimes={}))
        self.assertGreater(lc.lcoe, noLead.lcoe)
        self.assertAlmostEqual(lc.totalSpend, noLead.totalSpend)

    def test_quantities(self):
        core = economicsInterface.costCore(self.r.core, self.cs).fuelCost
        self.assertAlmostEqual(core.oreUranium * (1 - core.unitCosts.conversionLoss), core.feed)
        self.assertAlmostEqual(core.feed - core.uranium, core.tails)
        self.assertGreater(core.u3o8Lb, core.oreUranium * 2.2)

    def test_cycleSummaries(self):
        lc = economicsInterface.costCore(self.r.core, self.cs)
        summaries = lc.cycleSummaries()
        self.assertEqual(len(summaries), 4)
        self.assertAlmostEqual(sum(s.total for s in summaries), lc.totalSpend)
        self.assertAlmostEqual(sum(s.generation for s in summaries), lc.totalGeneration)

    def test_batchReloads(self):
        """A third-core reload costs a third of the initial core each later cycle."""
        cs = self.cs.modified(newSettings={econSettings.CONF_ECON_RELOAD_FRACTION: 1 / 3})
        lc = economicsInterface.costCore(self.r.core, cs)
        self.assertAlmostEqual(lc.costs[1].total, lc.fuelCost.total / 3)
        self.assertGreater(lc.cycleSummaries()[0].costPerMWh, lc.cycleSummaries()[1].costPerMWh)

    def test_sensitivities(self):
        lc = economicsInterface.costCore(self.r.core, self.cs)
        rows = {name: (low, high) for name, low, high, _, _ in lc.sensitivities()}
        low, high = rows[fuelCost.CATEGORY_LABELS["ore"]]
        self.assertLess(low, lc.lcoe)
        self.assertGreater(high, lc.lcoe)
        cfLow, cfHigh = rows["Capacity factor"]
        self.assertGreater(cfLow, cfHigh)

    def test_reloadsFromRun(self):
        """Fresh assemblies the fuel management loads at a cycle's start become that cycle's reload."""
        cs = self.cs.modified(newSettings={econSettings.CONF_ECON_RELOADS: "run"})
        interface = economicsInterface.EconomicsInterface(self.r, cs)
        interface.interactInit()
        self.assertEqual(interface.levelizedCost.source, "projected")

        old = next(a for a in self.r.core if fuelCost.isFresh(a))
        for b in old:
            b.p.percentBu = 1.0
        fresh = self.r.core.createAssemblyOfType(old.getType(), cs=cs)
        self.r.core.removeAssembly(old, discharge=True)
        self.r.core.add(fresh, old.spatialLocator)
        try:
            interface.interactBOC(1)
            interface.interactBOC(2)
            interface.interactEOL()
        finally:
            self.r.core.removeAssembly(fresh, discharge=False)
            self.r.core.add(old, fresh.spatialLocator)
            for b in old:
                b.p.percentBu = 0.0

        lc = interface.levelizedCost
        self.assertEqual(lc.source, "run")
        self.assertEqual([ch.cycle for ch in lc.reloads], [1])
        expected = sum(b.getHMMass() for b in fresh) * self.r.core.powerMultiplier / 1000.0
        self.assertAlmostEqual(lc.costs[1].heavyMetal, expected, places=6)
        self.assertIsNone(lc.cycleSummaries()[2].loaded)

    def test_reloadSourceAuto(self):
        self.assertFalse(economicsInterface.reloadsFromRun(self.cs.modified(newSettings={"fuelHandlerName": ""})))
        withHandler = self.cs.modified(newSettings={"fuelHandlerName": "SomeFuelHandler"})
        self.assertTrue(economicsInterface.reloadsFromRun(withHandler))

    def test_interfaceAndReport(self):
        interface = self.o.getInterface("economics")
        self.assertIsNotNone(interface)
        with directoryChangers.TemporaryDirectoryChanger():
            interface.cs = self.cs.modified(
                newSettings={econSettings.CONF_ECON_FUEL_COST_REPORT: True, econSettings.CONF_ECON_RELOADS: "projected"}
            )
            interface.interactInit()
            self.assertGreater(self.r.core.p.fuelCostInitialCore, 0.0)
            self.assertGreater(self.r.core.p.fuelCostLcoe, 0.0)
            self.assertTrue(os.path.exists(f"{self.cs.caseTitle}-fuel-cost.pdf"))

    def test_dischargeBurnup(self):
        """Whole-core reloads burn each load for one cycle: thermal energy of a cycle over the core's heavy metal."""
        lc = economicsInterface.costCore(self.r.core, self.cs)
        cycle = lc.cycles[0]
        perCycle = lc.thermalPowerMW * cycle.capacityFactor * cycle.length * 365.25
        self.assertAlmostEqual(lc.dischargeBurnup, perCycle / lc.fuelCost.heavyMetal)

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
