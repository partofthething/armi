"""Tests for the enrichment (SWU) calculator."""

import unittest

from armi.physics.economics import enrichment


class TestEnrichment(unittest.TestCase):
    def test_knownValues(self):
        """4.5 wt% from natural feed at 0.25% tails takes 9.22 kg feed and 6.87 kg-SWU per kg."""
        req = enrichment.enrich(10.0, 0.045)
        self.assertAlmostEqual(req.feed, 92.19, places=2)
        self.assertAlmostEqual(req.swu, 68.71, places=2)
        self.assertAlmostEqual(req.tails, req.feed - 10.0)

    def test_massAndU235Balance(self):
        req = enrichment.enrich(3.0, 0.065, tailsAssay=0.003)
        self.assertAlmostEqual(req.feed * req.feedAssay, req.product * 0.065 + req.tails * 0.003)

    def test_valueFunctionSymmetric(self):
        self.assertAlmostEqual(enrichment.valueFunction(0.5), 0.0)
        self.assertAlmostEqual(enrichment.valueFunction(0.2), enrichment.valueFunction(0.8))

    def test_belowNatural(self):
        """Sub-natural product is blended, not enriched; at or below tails it is free stock."""
        blended = enrichment.enrich(1.0, 0.005)
        self.assertEqual(blended.swu, 0.0)
        self.assertLess(blended.feed, 1.0)
        self.assertLess(blended.tails, 0.0)

        depleted = enrichment.enrich(1.0, 0.002)
        self.assertEqual((depleted.feed, depleted.swu), (0.0, 0.0))

    def test_badAssays(self):
        with self.assertRaises(ValueError):
            enrichment.feedFactor(0.05, feedAssay=0.002, tailsAssay=0.003)

    def test_feedCost(self):
        """$100/lb U3O8 is about $260/kgU."""
        self.assertAlmostEqual(enrichment.feedCostPerKgU(100.0), 259.97, places=1)
        self.assertAlmostEqual(enrichment.feedCostPerKgU(100.0, 10.0, 0.5), 2 * 259.97 + 10.0, places=1)

    def test_optimumTails(self):
        """Dear uranium and cheap separative work drive the optimum tails assay down."""
        feedCost, swuCost = 250.0, 150.0
        optimum = enrichment.optimumTailsAssay(feedCost, swuCost)

        def cost(tails):
            req = enrichment.enrich(1.0, 0.05, tailsAssay=tails)
            return req.feed * feedCost + req.swu * swuCost

        self.assertLess(cost(optimum), cost(optimum + 3e-4))
        self.assertLess(cost(optimum), cost(optimum - 3e-4))
        self.assertLess(enrichment.optimumTailsAssay(2 * feedCost, swuCost), optimum)
