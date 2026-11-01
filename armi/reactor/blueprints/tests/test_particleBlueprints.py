"""Tests for particles dispersed in components (TRISO fuel and the like)."""

import inspect
import math
import re
import unittest

from armi import settings
from armi.reactor import blueprints
from armi.reactor.components import particles
from armi.reactor.components.particles import ParticleLayer, sphereVolume
from armi.reactor.flags import Flags

# The MHTGR-350 benchmark's TRISO (NEA/NSC/R(2017)4, Table I.4), in a block of 210 compacts.
TRISO_BLUEPRINT = inspect.cleandoc(
    r"""
    nuclide flags:
        U235: {burn: false, xs: true}
        U238: {burn: false, xs: true}
        O: {burn: false, xs: true, expandTo: [O16]}
        C: {burn: false, xs: true}
        SI: {burn: false, xs: true}
    custom isotopics:
        UCO:
            input format: number densities
            U235: 3.8e-3
            U238: 2.06e-2
            O16: 3.66e-2
            C: 1.22e-2
        SiC:
            input format: number densities
            SI: 4.81e-2
            C: 4.81e-2
        buffer carbon:
            input format: number densities
            C: 5.01e-2
    particles:
        TRISO:
            kernel:
                material: Custom
                isotopics: UCO
                od: 0.0425
                flags: fuel depletable
            buffer:
                material: Custom
                isotopics: buffer carbon
                od: 0.0625
            inner PyC:
                material: Graphite
                od: 0.0695
            SiC:
                material: Custom
                isotopics: SiC
                od: 0.0765
            outer PyC:
                material: Graphite
                od: 0.0845
    blocks:
        fuel: &block_fuel
            fuel compact:
                shape: Circle
                material: Graphite
                Tinput: TINPUT
                Thot: THOT
                od: 1.245
                mult: 210
                particles: TRISO
                packingFraction: 0.35
            coolant:
                shape: Circle
                material: Void
                Tinput: 25.0
                Thot: 25.0
                od: 1.588
                mult: 102
            block graphite:
                shape: DerivedShape
                material: Graphite
                Tinput: 25.0
                Thot: 25.0
            outline:
                shape: Hexagon
                material: Void
                Tinput: 25.0
                Thot: 25.0
                ip: 36.0
                op: 36.0
    assemblies:
        fuel column:
            specifier: F
            blocks: [*block_fuel]
            height: [79.3]
            axial mesh points: [1]
            xs types: [A]
    """
)


def _text(tinput=25.0, thot=25.0):
    return TRISO_BLUEPRINT.replace("TINPUT", str(tinput)).replace("THOT", str(thot))


def _block(tinput=25.0, thot=25.0):
    bp = blueprints.Blueprints.load(_text(tinput, thot))
    cs = settings.Settings()
    return bp.constructAssem(cs, "fuel column")[0]


class TestParticleFuel(unittest.TestCase):
    def setUp(self):
        self.b = _block()
        self.compact = self.b.getComponentByName("fuel compact")
        self.layers = [c for c in self.b if isinstance(c, ParticleLayer)]

    def test_layersAreComponentsOfTheBlock(self):
        names = [c.name for c in self.layers]
        self.assertEqual(
            names,
            [
                "fuel compact kernel",
                "fuel compact buffer",
                "fuel compact inner PyC",
                "fuel compact SiC",
                "fuel compact outer PyC",
            ],
        )
        self.assertIs(self.compact.particleLayers[0], self.layers[0])
        self.assertIs(self.layers[0].host, self.compact)
        # each layer starts where the one inside it ends
        for inner, outer in zip(self.layers, self.layers[1:], strict=False):
            self.assertAlmostEqual(outer.getDimension("id"), inner.getDimension("od"))

    def test_particleCountFromPackingFraction(self):
        outline = 210 * math.pi * 1.245**2 / 4.0
        perCm = 0.35 * outline / sphereVolume(0.0845)
        for layer in self.layers:
            self.assertAlmostEqual(layer.getDimension("mult"), perCm)
        # 6,647 particles in each 4.928 cm compact. (The benchmark's approximate core totals, 1.2186e10 particles in
        # 2.0358e6 compacts, imply 5,986: its packing fraction and its counts disagree by about 10%.)
        perCompact = self.layers[0].getParticleCount() / 210 / (79.3 / 4.928)
        self.assertAlmostEqual(perCompact, 0.35 * math.pi * 0.6225**2 * 4.928 / sphereVolume(0.0845))
        self.assertAlmostEqual(perCompact, 6647, delta=1)

    def test_compactIsMatrixPlusParticles(self):
        outline = 210 * math.pi * 1.245**2 / 4.0
        particleArea = sum(layer.getArea() for layer in self.layers)
        self.assertAlmostEqual(self.compact.getArea() + particleArea, outline)
        self.assertAlmostEqual(particleArea / outline, 0.35)
        self.assertEqual(self.compact.p.packingFraction, 0.35)

    def test_blockAreaIsConserved(self):
        hexagon = math.sqrt(3.0) / 2.0 * 36.0**2
        self.assertAlmostEqual(sum(c.getArea() for c in self.b), hexagon, places=6)

    def test_kernelMassScalesWithHeight(self):
        kernel = self.layers[0]
        perParticle = kernel.getMass() / kernel.getParticleCount()
        self.b.setHeight(2 * self.b.getHeight())
        self.assertAlmostEqual(kernel.getMass() / kernel.getParticleCount(), perParticle)
        self.assertAlmostEqual(kernel.getParticleCount(), 2 * 79.3 * kernel.getDimension("mult"))

    def test_flagsComeFromTheLayers(self):
        self.assertTrue(self.layers[0].hasFlags(Flags.FUEL | Flags.DEPLETABLE))
        self.assertFalse(self.layers[1].hasFlags(Flags.FUEL))
        # 210 compacts, not 10^5 particles, are the block's pins
        self.assertEqual(self.b.getNumPins(), 210)

    def test_homogenizedNumberDensitiesHoldTheKernels(self):
        kernel = self.layers[0]
        expected = kernel.getNumberDensity("U235") * kernel.getVolume() / self.b.getVolume()
        self.assertAlmostEqual(self.b.getNumberDensity("U235"), expected)


class TestComponentOrigin(unittest.TestCase):
    def test_originPlacesAComponentOffCenter(self):
        channel = (
            r"\1control channel:\n\1    shape: Circle\n\1    material: Void\n\1    Tinput: 25.0\n"
            r"\1    Thot: 25.0\n\1    od: 9.525\n\1    origin: [8.449, 4.878]\n\1block graphite:"
        )
        text = re.sub(r"( +)block graphite:", channel, _text(), count=1)
        b = blueprints.Blueprints.load(text).constructAssem(settings.Settings(), "fuel column")[0]
        channel = b.getComponentByName("control channel")
        x, y, _ = channel.spatialLocator.getLocalCoordinates()
        self.assertAlmostEqual(x, 8.449)
        self.assertAlmostEqual(y, 4.878)
        self.assertAlmostEqual(channel.getArea(), math.pi * 9.525**2 / 4.0)


class TestPrismaticElement(unittest.TestCase):
    """Compacts and a coolant channel on lattice sites of their own, as in an HTGR fuel element."""

    def test_coolantOnItsOwnSites(self):
        grid = inspect.cleandoc(
            """
            grids:
                element:
                    geom: hex_corners_up
                    symmetry: full
                    lattice pitch:
                        hex: 1.8796
                    grid contents:
                        [0, 0]: C
                        [1, 0]: F
                        [0, 1]: F
                        [-1, 1]: F
                        [-1, 0]: F
                        [0, -1]: F
                        [1, -1]: F
            """
        )
        text = _text().replace("        fuel compact:\n", "        grid name: element\n        fuel compact:\n", 1)
        text = text.replace("            mult: 210\n", "            latticeIDs: [F]\n", 1)
        text = text.replace("            mult: 102\n", "            latticeIDs: [C]\n", 1)
        b = blueprints.Blueprints.load(text + "\n" + grid).constructAssem(settings.Settings(), "fuel column")[0]
        self.assertEqual(b.getComponentByName("fuel compact").getDimension("mult"), 6)
        self.assertEqual(b.getComponentByName("coolant").getDimension("mult"), 1)
        b.assignPinIndices()  # as placing the block in a core does; used to fail on the coolant site
        self.assertEqual(len(b.getComponentByName("fuel compact").p.pinIndices), 6)
        self.assertIsNone(b.getComponentByName("coolant").p.pinIndices)
        kernel = b.getComponentByName("fuel compact kernel")
        self.assertAlmostEqual(kernel.getDimension("mult"), 0.35 * 6 * math.pi * 1.245**2 / 4 / sphereVolume(0.0845))


class TestParticleFuelExpansion(unittest.TestCase):
    def test_particleCountFixedWhenTheHostExpands(self):
        cold = _block(25.0, 25.0)
        hot = _block(25.0, 500.0)
        layer = next(c for c in hot if isinstance(c, ParticleLayer))
        coldLayer = next(c for c in cold if isinstance(c, ParticleLayer))
        self.assertAlmostEqual(layer.getDimension("mult"), coldLayer.getDimension("mult"))
        compact = hot.getComponentByName("fuel compact")
        self.assertGreater(compact.getArea(), cold.getComponentByName("fuel compact").getArea())


class TestParticleFuelInputErrors(unittest.TestCase):
    def test_packingFractionNeedsParticles(self):
        text = re.sub(r"\n +particles: TRISO", "", _text(), count=1)
        with self.assertRaisesRegex(ValueError, "packingFraction but no particles"):
            blueprints.Blueprints.load(text).constructAssem(settings.Settings(), "fuel column")

    def test_unknownParticles(self):
        text = _text().replace("particles: TRISO", "particles: BISO", 1)
        with self.assertRaisesRegex(ValueError, "does not define"):
            blueprints.Blueprints.load(text).constructAssem(settings.Settings(), "fuel column")

    def test_packingFractionRange(self):
        host = _block().getComponentByName("fuel compact")
        with self.assertRaises(ValueError):
            particles.attachParticleLayers(host, host.particleLayers, 0.8)
