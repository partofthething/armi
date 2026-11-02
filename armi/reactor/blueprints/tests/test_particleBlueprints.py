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

    def test_packingFractionCanBeStored(self):
        """The packing fraction is a plain float, which the database can write next to the Nones of other components."""
        import numpy as np

        from armi.bookkeeping.db import layout

        self.assertIs(type(self.compact.p.packingFraction), float)
        values = np.array([c.p.packingFraction for c in self.b], dtype=object)
        stored = layout.replaceNonesWithNonsense(values, "packingFraction")
        self.assertEqual(stored[0], 0.35)

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


# A LEUPRO-1 pebble (IAEA-TECDOC-1249) as a spherical unit cell: 9,393.6 particles in a 5 cm fuel zone, a 0.5 cm
# graphite shell, and the pebble's share of the bed out to 3.7959 cm.
PEBBLE_BLUEPRINT = inspect.cleandoc(
    r"""
    nuclide flags:
        U235: {burn: false, xs: true}
        U238: {burn: false, xs: true}
        O: {burn: false, xs: true, expandTo: [O16]}
        C: {burn: false, xs: true}
    custom isotopics:
        UO2:
            input format: number densities
            U235: 4.11729e-3
            U238: 2.01906e-2
            O16: 4.86157e-2
        carbon:
            input format: number densities
            C: 8.64563e-2
        bed:
            input format: number densities
            C: 4.08644e-2
    particles:
        coated particle:
            kernel:
                material: Custom
                isotopics: UO2
                od: 0.0502
                flags: fuel depletable
            coatings:
                material: Custom
                isotopics: carbon
                od: 0.09154
    blocks:
        pebble cell: &block_pebble
            fuel zone:
                shape: Sphere
                material: Custom
                isotopics: carbon
                Tinput: 20.0
                Thot: 20.0
                id: 0.0
                od: 5.0
                mult: 1
                particles: coated particle
                packingFraction: PACKING
            shell:
                shape: Sphere
                material: Custom
                isotopics: carbon
                Tinput: 20.0
                Thot: 20.0
                id: 5.0
                od: 6.0
                mult: 1
            bed:
                shape: Sphere
                material: Custom
                isotopics: bed
                Tinput: 20.0
                Thot: 20.0
                id: 6.0
                od: 7.5918
                mult: 1
    assemblies:
        pebble:
            specifier: P
            blocks: [*block_pebble]
            height: [10.0]
            axial mesh points: [1]
            xs types: [A]
    """
)
PEBBLE_PARTICLES = 9393.6


class TestPebbleCell(unittest.TestCase):
    def setUp(self):
        packing = PEBBLE_PARTICLES * sphereVolume(0.09154) / sphereVolume(5.0)
        bp = blueprints.Blueprints.load(PEBBLE_BLUEPRINT.replace("PACKING", repr(packing)))
        self.b = bp.constructAssem(settings.Settings(), "pebble")[0]
        self.zone = self.b.getComponentByName("fuel zone")
        self.kernel = self.b.getComponentByName("fuel zone kernel")

    def test_aSphereMakesASphericalBlock(self):
        from armi.reactor.blocks import SphericalBlock

        self.assertIsInstance(self.b, SphericalBlock)
        self.assertAlmostEqual(self.b.getPitch(), 7.5918)

    def test_particlesInASphereAreCounted(self):
        self.assertTrue(self.kernel.is3D)
        self.assertAlmostEqual(self.kernel.getParticleCount(), PEBBLE_PARTICLES)
        self.assertAlmostEqual(self.kernel.getVolume(), PEBBLE_PARTICLES * sphereVolume(0.0502))

    def test_volumesAreTheSpheres(self):
        particles = sum(c.getVolume() for c in self.zone.particleLayers)
        self.assertAlmostEqual(self.zone.getVolume() + particles, sphereVolume(5.0))
        self.assertAlmostEqual(sum(c.getVolume() for c in self.b), sphereVolume(7.5918))
        self.assertAlmostEqual(self.b.getVolume(), sphereVolume(7.5918))

    def test_volumesDoNotDependOnTheBlockHeight(self):
        mass = self.b.getMass("U235")
        self.b.setHeight(2.0 * self.b.getHeight())
        self.assertAlmostEqual(self.b.getMass("U235"), mass)
        self.assertAlmostEqual(self.kernel.getParticleCount(), PEBBLE_PARTICLES)

    def test_homogenizedNumberDensitiesHoldTheKernels(self):
        expected = 4.11729e-3 * PEBBLE_PARTICLES * sphereVolume(0.0502) / sphereVolume(7.5918)
        self.assertAlmostEqual(self.b.getNumberDensity("U235") / expected, 1.0)


class TestParticlesInTheDatabase(unittest.TestCase):
    """Particle layers come back from a database dispersed in their host, as the blueprints built them."""

    SYSTEMS = inspect.cleandoc(
        """
        systems:
            core:
                grid name: core
                origin: {x: 0.0, y: 0.0, z: 0.0}
        grids:
            core:
                geom: GEOM
                symmetry: full
                lattice pitch: PITCH
                lattice map: |
                    SPEC
        """
    )

    def _roundTrip(self, text, geom, pitch, spec):
        from armi.bookkeeping.db import Database
        from armi.reactor import reactors
        from armi.utils.directoryChangers import TemporaryDirectoryChanger

        systems = self.SYSTEMS.replace("GEOM", geom).replace("PITCH", pitch).replace("SPEC", spec)
        cs = settings.Settings().modified(newSettings={"nCycles": 1, "burnSteps": 0})
        bp = blueprints.Blueprints.load(text + "\n" + systems)
        r = reactors.factory(cs, bp)
        with TemporaryDirectoryChanger():
            with Database("particles.h5", "w") as db:
                db.writeToDB(r)
            with Database("particles.h5", "r") as db:
                loaded = db.load(0, 0, cs=cs, bp=bp)
        return r.core.getFirstBlock(), loaded.core.getFirstBlock()

    def _assertSame(self, built, loaded, host):
        layers = [c for c in loaded if isinstance(c, ParticleLayer)]
        hostLoaded = loaded.getComponentByName(host)
        self.assertEqual([c.name for c in hostLoaded.particleLayers], [c.name for c in layers])
        self.assertTrue(all(c.host is hostLoaded for c in layers))
        for name in [host, *(c.name for c in layers)]:
            self.assertAlmostEqual(
                loaded.getComponentByName(name).getVolume(), built.getComponentByName(name).getVolume()
            )
        self.assertAlmostEqual(loaded.getVolume(), built.getVolume())
        self.assertAlmostEqual(loaded.getMass("U235"), built.getMass("U235"))

    def test_compacts(self):
        built, loaded = self._roundTrip(_text(), "hex", "{hex: 36.0}", "F")
        self._assertSame(built, loaded, "fuel compact")

    def test_pebble(self):
        packing = PEBBLE_PARTICLES * sphereVolume(0.09154) / sphereVolume(5.0)
        text = PEBBLE_BLUEPRINT.replace("PACKING", repr(packing))
        built, loaded = self._roundTrip(text, "cartesian", "{x: 7.5918, y: 7.5918}", "P")
        self._assertSame(built, loaded, "fuel zone")
        self.assertTrue(loaded.getComponentByName("fuel zone kernel").is3D)
