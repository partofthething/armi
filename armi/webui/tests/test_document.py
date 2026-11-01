# Copyright 2026 Ocean Atomics, LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""Tests for turning blueprints into an editable document and back."""

import json
import os
import textwrap
import unittest

from armi import configure, isConfigured

if not isConfigured():
    configure()

from armi.webui.document import BlueprintsDocument, DocumentError, cellKey, parseCellKey
from armi.webui.tests.helpers import inTempDir

# A full-core Cartesian core with a pin lattice, an assembly whose stack repeats one block design,
# and a material modification that varies up that stack. Between them these cover every shape of
# data the document has to carry across the browser and back.
SAMPLE = """
nuclide flags:
  U235: {burn: false, xs: true}
  U238: {burn: false, xs: true}
  O: {burn: false, xs: true, expandTo: [O16]}
  FE: {burn: false, xs: true}

blocks:
  reflector: &reflector
    steel:
      shape: UnshapedComponent
      material: HT9
      Tinput: 20.0
      Thot: 20.0
      area: 100.0
    coolant:
      shape: DerivedShape
      material: Sodium
      Tinput: 20.0
      Thot: 20.0
    duct:
      shape: Square
      material: HT9
      Tinput: 20.0
      Thot: 20.0
      widthInner: 15.0
      widthOuter: 16.0
  fuel: &fuel
    grid name: pins
    fuel:
      shape: Circle
      material: UZr
      Tinput: 20.0
      Thot: 600.0
      od: 0.8
      latticeIDs: [F]
    gap:
      shape: Circle
      material: Sodium
      Tinput: 20.0
      Thot: 600.0
      id: fuel.od
      od: clad.id
      latticeIDs: [F]
    clad:
      shape: Circle
      material: HT9
      Tinput: 20.0
      Thot: 600.0
      id: 0.9
      od: 1.0
      latticeIDs: [F]
    coolant:
      shape: DerivedShape
      material: Sodium
      Tinput: 20.0
      Thot: 600.0
    duct:
      shape: Square
      material: HT9
      Tinput: 20.0
      Thot: 20.0
      widthInner: 15.0
      widthOuter: 16.0

assemblies:
  driver:
    specifier: DR
    flags: fuel
    xs types: [A, A, A]
    height: [20.0, 50.0, 20.0]
    axial mesh points: [1, 2, 1]
    blocks: [*reflector, *fuel, *reflector]
    material modifications:
      U235_wt_frac: [null, 0.11, null]
  blank:
    specifier: BL
    flags: reflector
    xs types: [A, A, A]
    height: [20.0, 50.0, 20.0]
    axial mesh points: [1, 1, 1]
    blocks: [*reflector, *reflector, *reflector]

systems:
  core:
    grid name: core
    origin: {x: 0.0, y: 0.0, z: 0.0}

grids:
  core:
    geom: cartesian
    symmetry: full
    lattice pitch: {x: 16.0, y: 16.0}
    lattice map: |
      BL BL BL
      BL DR BL
      BL BL BL
  pins:
    geom: cartesian
    symmetry: full
    lattice pitch: {x: 1.1, y: 1.1}
    lattice map: |
      F F F
      F F F
      F F F
"""


class TestCellKeys(unittest.TestCase):
    def test_roundTrip(self):
        self.assertEqual(parseCellKey(cellKey(-3, 4)), (-3, 4))

    def test_toleratesWhitespace(self):
        self.assertEqual(parseCellKey(" -2 , 7 "), (-2, 7))

    def test_rejectsNonsense(self):
        with self.assertRaises(DocumentError):
            parseCellKey("over there")


class TestBlueprintsDocument(unittest.TestCase):
    def setUp(self):
        self.doc = BlueprintsDocument.fromText(SAMPLE)
        self.data = self.doc.toDict()

    def test_gridsArriveAsCells(self):
        """Ascii maps are an input convenience; the editor only ever sees positions."""
        core = self.data["grids"]["core"]
        self.assertNotIn("lattice map", core)
        self.assertNotIn("grid contents", core)
        self.assertEqual(len(core["cells"]), 9)
        self.assertEqual(core["cells"]["0,0"], "DR")

    def test_fullCartesianCellsAreCenteredOnOrigin(self):
        keys = [parseCellKey(key) for key in self.data["grids"]["core"]["cells"]]
        self.assertEqual(min(i for i, _ in keys), -1)
        self.assertEqual(max(i for i, _ in keys), 1)

    def test_assemblyStacksAreBlockNames(self):
        """Aliases to one anchored block must not become several unrelated copies."""
        self.assertEqual(self.data["assemblies"]["driver"]["blocks"], ["reflector", "fuel", "reflector"])

    def test_geometryCarriesTheGridBasis(self):
        geom = self.doc.geometryOf("core")
        self.assertEqual(geom["shape"], "square")
        self.assertFalse(geom["cornersUp"])
        self.assertTrue(geom["hasPitch"])
        self.assertAlmostEqual(geom["iStep"][0], 16.0)
        self.assertAlmostEqual(geom["jStep"][1], 16.0)

    def test_paintablePositionsRingTheGrid(self):
        paintable = set(self.doc.geometryOf("core")["paintable"])
        self.assertIn("2,0", paintable)
        self.assertIn("-2,-2", paintable)
        # Nothing already occupied is offered as an empty position to paint into.
        self.assertFalse(paintable & set(self.data["grids"]["core"]["cells"]))

    def test_documentSurvivesTheBrowser(self):
        """A round trip through JSON must not change the document."""
        self.doc.apply(json.loads(json.dumps(self.data)))
        self.assertEqual(self.doc.toDict(), self.data)

    def test_blockSharingSurvivesTheBrowser(self):
        self.doc.apply(json.loads(json.dumps(self.data)))
        rebuilt = BlueprintsDocument.fromText(self.doc.toYaml())
        self.assertIs(rebuilt.bp.assemDesigns["driver"].blocks[0], rebuilt.bp.blockDesigns["reflector"])
        self.assertIs(rebuilt.bp.assemDesigns["driver"].blocks[2], rebuilt.bp.blockDesigns["reflector"])

    def test_wholeNumbersStayFloats(self):
        """JSON.stringify turns 20.0 into 20; opening and saving must not rewrite every number."""
        payload = json.loads(json.dumps(self.data))
        self.assertEqual(payload["assemblies"]["driver"]["height"][0], 20)
        self.doc.apply(payload)
        self.assertIn("20.0", self.doc.toYaml())

    def test_paintingACell(self):
        payload = json.loads(json.dumps(self.data))
        payload["grids"]["core"]["cells"]["0,0"] = "BL"
        payload["grids"]["core"]["cells"]["2,2"] = "DR"
        self.doc.apply(payload)

        after = self.doc.toDict()["grids"]["core"]["cells"]
        self.assertEqual(after["0,0"], "BL")
        self.assertEqual(after["2,2"], "DR")

        # And it survives being written out as an ascii map and read back.
        reloaded = BlueprintsDocument.fromText(self.doc.toYaml()).toDict()
        self.assertEqual(reloaded["grids"]["core"]["cells"]["2,2"], "DR")

    def test_paintingSkipsRevalidatingTheWholeFile(self):
        """Painting is the one edit that happens by the dozen, so it takes a shorter route.

        The shortcut has to be invisible: what comes back must be what a full rebuild would give.
        """
        payload = json.loads(json.dumps(self.data))
        payload["grids"]["core"]["cells"]["0,0"] = "BL"
        self.doc.apply(payload)
        shortcut = self.doc.toDict()

        self.doc._invalidate()
        self.assertEqual(self.doc.toDict(), shortcut)

    def test_anEditOutsideTheGridsTakesTheLongRoute(self):
        payload = json.loads(json.dumps(self.data))
        payload["grids"]["core"]["cells"]["0,0"] = "BL"
        payload["assemblies"]["driver"]["height"][1] = 51.0
        self.doc.apply(payload)

        after = self.doc.toDict()
        self.assertEqual(after["grids"]["core"]["cells"]["0,0"], "BL")
        self.assertEqual(after["assemblies"]["driver"]["height"][1], 51.0)

    def test_theShortcutStillRefusesRubbish(self):
        payload = json.loads(json.dumps(self.data))
        payload["grids"]["core"]["cells"]["over there"] = "BL"
        with self.assertRaises(DocumentError):
            self.doc.apply(payload)

    def test_erasingACell(self):
        payload = json.loads(json.dumps(self.data))
        del payload["grids"]["core"]["cells"]["1,1"]
        self.doc.apply(payload)
        self.assertNotIn("1,1", self.doc.toDict()["grids"]["core"]["cells"])

    def test_refusesAnEditArmiCannotRead(self):
        payload = json.loads(json.dumps(self.data))
        payload["assemblies"]["driver"]["height"] = "as tall as it needs to be"
        with self.assertRaises(DocumentError):
            self.doc.apply(payload)

    def test_refusesAStackPointingAtNothing(self):
        payload = json.loads(json.dumps(self.data))
        payload["assemblies"]["driver"]["blocks"] = ["reflector", "imaginary", "reflector"]
        with self.assertRaisesRegex(DocumentError, "imaginary"):
            self.doc.apply(payload)

    def test_blankStartsWithACoreGrid(self):
        doc = BlueprintsDocument.blank(geom="hex", symmetry="third periodic")
        data = doc.toDict()
        self.assertIn("core", data["grids"])
        self.assertEqual(data["systems"]["core"]["grid name"], "core")

    def test_expandToFull(self):
        doc = BlueprintsDocument.fromText(SAMPLE)
        before = len(doc.toDict()["grids"]["core"]["cells"])
        doc.expandToFull("core")
        self.assertGreaterEqual(len(doc.toDict()["grids"]["core"]["cells"]), before)


class TestSaving(unittest.TestCase):
    def test_writesAndReloads(self):
        with inTempDir():
            doc = BlueprintsDocument.fromText(SAMPLE)
            written = doc.save("out/bp.yaml")
            self.assertTrue(os.path.exists(written))
            self.assertEqual(BlueprintsDocument.fromFile(written).toDict()["grids"]["core"]["cells"]["0,0"], "DR")

    def test_refusesToSaveWithoutAPath(self):
        with self.assertRaises(DocumentError):
            BlueprintsDocument.fromText(SAMPLE).save()

    def test_includesAreFollowedButNotSilentlyFlattened(self):
        """Overwriting a file built from !includes would delete the structure someone set up."""
        with inTempDir():
            head, _, gridSection = SAMPLE.partition("grids:\n")
            with open("grids.yaml", "w") as stream:
                # The included file holds the entries themselves; the including line supplies the
                # key and the indentation.
                stream.write(textwrap.dedent(gridSection))
            with open("bp.yaml", "w") as stream:
                stream.write(head)
                # The !include sits on its own line; its column is where the included
                # content lands, which is how ARMI's own multi-file blueprints are written.
                stream.write("grids:\n  !include grids.yaml\n")

            doc = BlueprintsDocument.fromFile("bp.yaml")
            self.assertEqual(len(doc.includes), 1)
            self.assertEqual(doc.toDict()["grids"]["core"]["cells"]["0,0"], "DR")

            with self.assertRaisesRegex(DocumentError, "flatten"):
                doc.save()

            # Saving somewhere else is always fine, and so is saying to flatten.
            self.assertTrue(os.path.exists(doc.save("elsewhere.yaml")))

            # Flattening on purpose is allowed, and the result no longer needs the other file.
            doc.save("bp.yaml", flatten=True)
            os.remove("grids.yaml")
            self.assertEqual(BlueprintsDocument.fromFile("bp.yaml").toDict()["grids"]["core"]["cells"]["0,0"], "DR")


class TestOffCentreCartesianGrid(unittest.TestCase):
    """A full-core Cartesian grid that grows lopsided cannot be written as an ascii map.

    The map carries no origin of its own, so the reader has to assume the contents are centered.
    ARMI falls back to writing the positions out explicitly, which is correct but much harder to
    read, so the editor needs to know it has happened.
    """

    def setUp(self):
        self.doc = BlueprintsDocument.fromText(SAMPLE)
        self.data = self.doc.toDict()

    def test_aTidyGridIsCentered(self):
        self.assertTrue(self.doc.geometryOf("core")["centered"])

    def test_paintingOutsideTheExtentIsNoticed(self):
        payload = json.loads(json.dumps(self.data))
        payload["grids"]["core"]["cells"]["0,2"] = "BL"
        self.doc.apply(payload)
        self.assertFalse(self.doc.geometryOf("core")["centered"])

    def test_theFileIsStillCorrect(self):
        """The fallback has to keep every position, even though it is uglier."""
        payload = json.loads(json.dumps(self.data))
        payload["grids"]["core"]["cells"]["0,2"] = "BL"
        self.doc.apply(payload)

        reloaded = BlueprintsDocument.fromText(self.doc.toYaml()).toDict()
        self.assertEqual(reloaded["grids"]["core"]["cells"], self.doc.toDict()["grids"]["core"]["cells"])
        self.assertEqual(reloaded["grids"]["core"]["cells"]["0,2"], "BL")
        self.assertEqual(reloaded["grids"]["core"]["cells"]["0,0"], "DR")

    def test_squaringItUpRestoresTheMap(self):
        payload = json.loads(json.dumps(self.data))
        for j in (-2, 2):
            for i in (-1, 0, 1):
                payload["grids"]["core"]["cells"][f"{i},{j}"] = "BL"
        for i in (-2, 2):
            for j in (-2, -1, 0, 1, 2):
                payload["grids"]["core"]["cells"][f"{i},{j}"] = "BL"
        self.doc.apply(payload)

        self.assertTrue(self.doc.geometryOf("core")["centered"])
        self.assertIn("lattice map", self.doc.toYaml())
