"""Tests for levelized costs."""

import unittest

from armi.physics.economics import levelized
from armi.settings import caseSettings


class TestLevelized(unittest.TestCase):
    def test_presentValue(self):
        self.assertAlmostEqual(levelized.presentValue([(0, 100.0), (1, 110.0)], 0.1), 200.0)

    def test_undiscounted(self):
        """With no discounting the levelized cost is total cost over total generation."""
        lcoe = levelized.levelizedCost([(0, 300.0), (5, 300.0)], [(1, 10.0), (6, 20.0)], 0.0)
        self.assertAlmostEqual(lcoe, 20.0)

    def test_cyclesFromSettings(self):
        cs = caseSettings.Settings().modified(
            newSettings={"nCycles": 3, "cycleLength": 730.5, "availabilityFactor": 0.9, "burnSteps": 2}
        )
        cycles = levelized.cyclesFromSettings(cs)
        self.assertEqual([c.start for c in cycles], [0.0, 2.0, 4.0])
        self.assertAlmostEqual(cycles[1].midpoint, 3.0)
        self.assertAlmostEqual(cycles[0].capacityFactor, 0.9)

        generation = levelized.generationByCycle(cycles, 100.0)
        self.assertAlmostEqual(generation[0][1], 100.0 * 0.9 * 2 * 8766)
