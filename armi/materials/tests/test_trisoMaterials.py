"""Tests for the materials of TRISO particle fuel and gas-cooled reactors: SiC, UCO and helium."""

import unittest

from armi import materials


class TestSiC(unittest.TestCase):
    def setUp(self):
        self.mat = materials.createMaterialByName("SiC")

    def test_composition(self):
        self.assertAlmostEqual(self.mat.massFrac["SI"], 28.0855 / (28.0855 + 12.011), places=3)

    def test_densityAtRoomTemperature(self):
        self.assertAlmostEqual(self.mat.density(Tc=20.0), 3.21, places=4)

    def test_expansionFollowsSnead(self):
        # mean expansion coefficient about 4e-6/K from 20 to 800 C (Snead et al. 2007, Fig. 2)
        self.assertAlmostEqual(self.mat.linearExpansionPercent(Tc=20.0), 0.0, places=6)
        alpha = self.mat.linearExpansionPercent(Tc=800.0) / 100.0 / 780.0
        self.assertGreater(alpha, 3.5e-6)
        self.assertLess(alpha, 4.8e-6)

    def test_massConservedWhenExpanding(self):
        """Density x (1 + dL/L)^3 is the room temperature density at any temperature."""
        for tc in (300.0, 800.0):
            grown = (1.0 + self.mat.linearExpansionPercent(Tc=tc) / 100.0) ** 3
            self.assertAlmostEqual(self.mat.density(Tc=tc) * grown, 3.21, places=6)

    def test_theoreticalDensityFraction(self):
        self.mat.applyInputParams(TD_frac=0.9)
        self.assertAlmostEqual(self.mat.pseudoDensity(Tc=20.0), 0.9 * 3.21, places=4)


class TestUCO(unittest.TestCase):
    def setUp(self):
        self.mat = materials.createMaterialByName("UCO")

    def test_stoichiometry(self):
        """UC0.5O1.5: half a carbon and one and a half oxygen per uranium atom."""
        mf = self.mat.massFrac
        uranium = mf["U235"] / 235.0439 + mf["U238"] / 238.0508
        self.assertAlmostEqual(mf["C"] / 12.011 / uranium, 0.5, places=2)
        self.assertAlmostEqual(mf["O"] / 15.999 / uranium, 1.5, places=2)

    def test_enrichmentAndDensity(self):
        """The MHTGR-350 benchmark kernel: 15.5 wt% enriched, 10.5 g/cm^3."""
        self.mat.applyInputParams(U235_wt_frac=0.155, TD_frac=10.5 / 11.03)
        mf = self.mat.massFrac
        self.assertAlmostEqual(mf["U235"] / (mf["U235"] + mf["U238"]), 0.155, places=6)
        self.assertAlmostEqual(self.mat.pseudoDensity(Tc=20.0), 10.5, delta=0.05)


class TestHelium(unittest.TestCase):
    def setUp(self):
        self.mat = materials.createMaterialByName("Helium")

    def test_idealGasDensity(self):
        # 0.1664 kg/m^3 at 20 C and 1 atm
        self.assertAlmostEqual(self.mat.density(Tc=20.0), 1.664e-4, delta=0.002e-4)
        self.assertAlmostEqual(self.mat.density(Tc=20.0) / self.mat.density(Tc=313.15), 2.0, places=6)

    def test_thermalConductivityKTA(self):
        # KTA 3102.1 at 1 bar: about 0.152 W/m-K at room temperature, 0.38 at 800 C
        self.assertAlmostEqual(self.mat.thermalConductivity(Tc=20.0), 0.152, delta=0.003)
        self.assertAlmostEqual(self.mat.thermalConductivity(Tc=800.0), 0.380, delta=0.005)

    def test_doesNotExpand(self):
        self.assertEqual(self.mat.linearExpansion(Tc=500.0), 0.0)
