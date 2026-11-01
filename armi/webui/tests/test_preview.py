"""Tests for laying a block out to be drawn."""

import math
import unittest

from armi import configure, isConfigured

if not isConfigured():
    configure()

from armi.webui import catalog, preview

PINS = {f"{i},{j}": ("G" if (i, j) == (0, 0) else "F") for i in (-1, 0, 1) for j in (-1, 0, 1)}
PIN_GEOM = {"iStep": [1.2, 0.0], "jStep": [0.0, 1.2], "shape": "square"}

PIN_BLOCK = {
    "grid name": "pins",
    "fuel": {"shape": "Circle", "material": "UZr", "Tinput": 20.0, "Thot": 600.0, "od": 0.8, "latticeIDs": ["F"]},
    "gap": {
        "shape": "Circle",
        "material": "Sodium",
        "Tinput": 20.0,
        "Thot": 600.0,
        "id": "fuel.od",
        "od": "clad.id",
        "latticeIDs": ["F"],
    },
    "clad": {
        "shape": "Circle",
        "material": "HT9",
        "Tinput": 20.0,
        "Thot": 600.0,
        "id": 0.9,
        "od": 1.0,
        "latticeIDs": ["F"],
    },
    "guide": {
        "shape": "Circle",
        "material": "HT9",
        "Tinput": 20.0,
        "Thot": 600.0,
        "id": 1.0,
        "od": 1.1,
        "latticeIDs": ["G"],
    },
    "coolant": {"shape": "DerivedShape", "material": "Sodium", "Tinput": 20.0, "Thot": 600.0},
    "duct": {"shape": "Square", "material": "HT9", "Tinput": 20.0, "Thot": 20.0, "widthInner": 3.6, "widthOuter": 3.6},
}


def _shapes():
    byKey = {}
    for shape in catalog.shapes():
        byKey[shape["key"]] = shape
        byKey[shape["name"].lower()] = shape
    return byKey


class TestDimensionLinks(unittest.TestCase):
    def test_followsALink(self):
        resolved = preview.resolveDimensions(
            {
                "fuel": {"od": 0.8},
                "gap": {"id": "fuel.od", "od": "clad.id"},
                "clad": {"id": 0.9, "od": 1.0},
            }
        )
        self.assertAlmostEqual(resolved["gap"]["id"], 0.8)
        self.assertAlmostEqual(resolved["gap"]["od"], 0.9)

    def test_followsAChainOfLinks(self):
        resolved = preview.resolveDimensions(
            {
                "a": {"od": 1.0},
                "b": {"od": "a.od"},
                "c": {"od": "b.od"},
                "d": {"od": "c.od"},
            }
        )
        self.assertAlmostEqual(resolved["d"]["od"], 1.0)

    def test_reportsALinkItCannotFollow(self):
        resolved = preview.resolveDimensions({"gap": {"id": "nothing.od"}})
        self.assertEqual(resolved["gap"]["_unresolved"], ["id"])

    def test_survivesACycle(self):
        """A half-finished edit can point two components at each other; that must not hang."""
        resolved = preview.resolveDimensions({"a": {"od": "b.od"}, "b": {"od": "a.od"}})
        self.assertEqual(resolved["a"]["_unresolved"], ["od"])
        self.assertEqual(resolved["b"]["_unresolved"], ["od"])


class TestBlockPreview(unittest.TestCase):
    def setUp(self):
        self.preview = preview.blockPreview(PIN_BLOCK, _shapes(), PINS, PIN_GEOM)
        self.byName = {component["name"]: component for component in self.preview["components"]}

    def test_componentsLandOnTheirLatticePositions(self):
        self.assertEqual(len(self.byName["fuel"]["positions"]), 8)
        self.assertEqual(len(self.byName["guide"]["positions"]), 1)
        self.assertEqual(self.byName["guide"]["positions"], [[0.0, 0.0]])

    def test_positionsAreInCentimetres(self):
        corners = {tuple(each) for each in self.byName["fuel"]["positions"]}
        self.assertIn((1.2, 1.2), corners)
        self.assertIn((-1.2, 0.0), corners)

    def test_fillerIsDrawnFirst(self):
        """Coolant has to go behind the pins, not on top of them."""
        order = [component["name"] for component in self.preview["components"]]
        self.assertLess(order.index("coolant"), order.index("fuel"))
        self.assertLess(order.index("clad"), order.index("fuel"))

    def test_areasAddUpToTheBlock(self):
        self.assertAlmostEqual(self.preview["footprint"], 3.6**2)
        self.assertAlmostEqual(self.byName["fuel"]["area"], math.pi / 4 * 0.8**2 * 8)
        self.assertAlmostEqual(self.byName["clad"]["area"], math.pi / 4 * (1.0**2 - 0.9**2) * 8)

        total = sum(c["area"] for c in self.preview["components"] if c["area"] is not None)
        self.assertAlmostEqual(total, self.preview["footprint"])

    def test_derivedShapeTakesWhatIsLeft(self):
        self.assertAlmostEqual(
            self.byName["coolant"]["area"],
            self.preview["footprint"] - self.preview["accounted"],
        )

    def test_aDuctOfZeroThicknessStillSetsTheFootprint(self):
        """Equal inner and outer widths give a boundary with no area of its own."""
        self.assertAlmostEqual(self.byName["duct"]["area"], 0.0)
        self.assertAlmostEqual(self.preview["footprint"], 3.6**2)

    def test_extentCoversTheWholeBlock(self):
        self.assertGreaterEqual(self.preview["extent"], 1.8)


class TestConcentricBlock(unittest.TestCase):
    def setUp(self):
        self.block = {
            "steel": {"shape": "UnshapedComponent", "material": "HT9", "Tinput": 20.0, "Thot": 20.0, "area": 100.0},
            "coolant": {"shape": "DerivedShape", "material": "Sodium", "Tinput": 20.0, "Thot": 20.0},
            "duct": {"shape": "Hexagon", "material": "HT9", "Tinput": 20.0, "Thot": 20.0, "ip": 15.0, "op": 16.0},
        }
        self.preview = preview.blockPreview(self.block, _shapes())
        self.byName = {c["name"]: c for c in self.preview["components"]}

    def test_everythingSitsAtTheCentre(self):
        for component in self.preview["components"]:
            self.assertEqual(component["positions"], [[0.0, 0.0]])

    def test_unshapedComponentReportsItsArea(self):
        self.assertAlmostEqual(self.byName["steel"]["area"], 100.0)

    def test_hexagonAreas(self):
        self.assertAlmostEqual(self.byName["duct"]["area"], math.sqrt(3) / 2 * (16.0**2 - 15.0**2))
        self.assertAlmostEqual(self.preview["footprint"], math.sqrt(3) / 2 * 16.0**2)

    def test_multiplicityCountsCopies(self):
        block = dict(self.block)
        block["wire"] = {
            "shape": "Circle",
            "material": "HT9",
            "Tinput": 20.0,
            "Thot": 20.0,
            "od": 0.2,
            "id": 0.0,
            "mult": 10.0,
        }
        result = preview.blockPreview(block, _shapes())
        wire = next(c for c in result["components"] if c["name"] == "wire")
        self.assertEqual(wire["count"], 10.0)
        self.assertAlmostEqual(wire["area"], math.pi / 4 * 0.2**2 * 10)


class TestCatalog(unittest.TestCase):
    def test_shapesCarryTheirDimensions(self):
        byName = {shape["name"]: shape for shape in catalog.shapes()}
        self.assertIn("od", byName["Circle"]["dimensions"])
        self.assertIn("id", byName["Circle"]["dimensions"])
        self.assertEqual(byName["Circle"]["drawAs"], "annulus")
        # Multiplicity is a count, not geometry, so it gets its own control.
        self.assertNotIn("mult", byName["Circle"]["dimensions"])
        self.assertTrue(byName["Circle"]["takesMult"])

    def test_derivedShapeIsOfferable(self):
        self.assertIn("DerivedShape", [shape["name"] for shape in catalog.shapes()])

    def test_materialsIncludeTheFramework(self):
        names = [material["name"] for material in catalog.materials()]
        self.assertIn("HT9", names)
        self.assertIn("Void", names)

    def test_geometriesOfferValidSymmetries(self):
        byGeom = {entry["geom"]: entry for entry in catalog.geometries()}
        self.assertIn("third periodic", byGeom["hex"]["symmetries"])
        self.assertIn("full through center", byGeom["cartesian"]["symmetries"])
        self.assertEqual(byGeom["cartesian"]["pitchKeys"], ["x", "y"])
        self.assertTrue(byGeom["hex_corners_up"]["cornersUp"])

    def test_flagsAreNames(self):
        flags = catalog.flags()
        self.assertIn("FUEL", flags)
        self.assertTrue(all(isinstance(flag, str) for flag in flags))
