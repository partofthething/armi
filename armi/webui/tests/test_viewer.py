"""Tests for reading results databases quickly, checked against ARMI's own loader."""

import http.client
import os
import shutil
import tempfile
import unittest

import h5py
import numpy as np

from armi import configure, isConfigured

if not isConfigured():
    configure()

from armi.bookkeeping.db.database import Database
from armi.webui import results
from armi.webui.api import Session
from armi.webui.server import TOKEN_HEADER, GuiServer
from armi.webui.syntheticResults import writeSyntheticDatabase


class _WithDatabase(unittest.TestCase):
    """Build one small database for the whole class: two cycles of two nodes, third core."""

    @classmethod
    def setUpClass(cls):
        cls.scratch = tempfile.mkdtemp(prefix="armi-webui-results-")
        cls.path = os.path.join(cls.scratch, "small.h5")
        writeSyntheticDatabase(cls.path, years=2, nodesPerYear=2, fullCore=False, split=False)
        cls.db = results.ResultsDatabase(cls.path)

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        shutil.rmtree(cls.scratch, ignore_errors=True)

    def loaded(self, key):
        """The reactor at a node, the slow and authoritative way."""
        cycle, node = int(key[1:3]), int(key[4:6])
        with Database(self.path, "r") as db:
            return db.load(cycle, node)


class TestResultsDatabase(_WithDatabase):
    def test_stepsInOrder(self):
        self.assertEqual(self.db.stepKeys(), ["c00n00", "c00n01", "c01n00", "c01n01"])
        self.assertEqual(self.db.steps[2]["time"], 1.0)

    def test_summaryDescribesEverything(self):
        summary = self.db.summary()
        self.assertEqual(len(summary["steps"]), 4)
        topo = summary["topologies"][summary["steps"][0]["topology"]]
        self.assertEqual(sum(topo["blocksPerAssembly"]), topo["blockCount"])
        self.assertEqual(summary["geometry"]["shape"], "hex")
        names = {entry["name"] for entry in summary["parameters"]["block"]}
        self.assertIn("pdens", names)
        self.assertIn("nU235", names)
        self.assertNotIn("flags", names)
        self.assertIn("U235", summary["parameters"]["componentNuclides"])

    def test_shufflingChangesTheFrameButNotTheTopology(self):
        frames = [self.db.positionsOf(key)[0] for key in self.db.stepKeys()]
        self.assertEqual(frames[0], frames[1])
        self.assertEqual(frames[2], frames[3])
        self.assertNotEqual(frames[0], frames[2])
        self.assertEqual(len({self.db.topologyOf(key).id for key in self.db.stepKeys()}), 1)

    def test_positionsMatchArmi(self):
        key = "c01n01"
        r = self.loaded(key)
        topo = self.db.topologyOf(key)
        _frame, ij = self.db.positionsOf(key)
        expected = {a.name: tuple(a.spatialLocator.getCompleteIndices()[:2]) for a in r.core}
        actual = {name: tuple(map(int, where)) for name, where in zip(topo.assemblyNames, ij)}
        self.assertEqual(actual, expected)

    def test_blockSeriesMatchesArmi(self):
        series = np.frombuffer(self.db.series("block", "pdens"), dtype="<f4")
        keys = self.db.stepKeys()
        offsets = np.cumsum([0] + [len(self.db.topologyOf(key).blockRows) for key in keys])
        self.assertEqual(len(series), offsets[-1])

        t = 3
        r = self.loaded(keys[t])
        topo = self.db.topologyOf(keys[t])
        expected = {b.name: b.p.pdens for b in r.core.iterBlocks()}
        for index, name in enumerate(topo.blockNames):
            self.assertAlmostEqual(series[offsets[t] + index], expected[name], delta=1e-4 * max(1, expected[name]))

    def test_assemblySeriesHasOneValuePerAssembly(self):
        series = np.frombuffer(self.db.series("assembly", "assemNum"), dtype="<f4")
        total = sum(len(self.db.topologyOf(key).assemblyRows) for key in self.db.stepKeys())
        self.assertEqual(len(series), total)
        self.assertTrue(np.all(np.isfinite(series)))

    def test_componentValuesMatchArmi(self):
        key = "c01n01"
        r = self.loaded(key)
        topo = self.db.topologyOf(key)
        temperatures = np.frombuffer(self.db.componentValues(key, "temperatureInC"), dtype="<f4")
        u235 = np.frombuffer(self.db.componentValues(key, "", nuclide="U235"), dtype="<f4")
        blocks = {b.name: b for b in r.core.iterBlocks()}
        for index in range(0, len(topo.blockRows), 7):
            block = blocks[topo.blockNames[index]]
            start, stop = topo.componentStart[index], topo.componentStart[index + 1]
            for comp in range(start, stop):
                component = block.getComponentByName(str(topo.componentNames[comp]))
                self.assertAlmostEqual(temperatures[comp], component.temperatureInC, places=2)
                self.assertAlmostEqual(u235[comp], component.getNumberDensity("U235"), delta=1e-8)

    def test_designsPlacePins(self):
        designs = self.db.designs(self.db.topologyOf("c00n00").id)
        self.assertEqual(len(designs["blockDesign"]), len(self.db.topologyOf("c00n00").blockRows))
        pinned = [shape for design in designs["designs"] for shape in design if shape.get("at")]
        self.assertTrue(pinned)
        for shape in pinned:
            self.assertEqual(len(shape["at"]) // 2, int(shape["mult"]))

    def test_componentHistory(self):
        topo = self.db.topologyOf("c00n00")
        fuel = next(index for index, name in enumerate(topo.blockNames) if self._isFuel(topo, index))
        values = self.db.componentHistory("temperatureInC", None, "fuel", [fuel] * 4)
        self.assertEqual(len(values), 4)
        self.assertTrue(all(value is not None for value in values))

    def _isFuel(self, topo, block):
        start, stop = topo.componentStart[block], topo.componentStart[block + 1]
        return "fuel" in topo.componentNames[start:stop]

    def test_symmetricCopiesForAThirdCore(self):
        _frame, ij = self.db.positionsOf("c00n00")
        copies = self.db.symmetricEquivalents(ij)
        self.assertIsNotNone(copies)
        self.assertEqual(len(copies), len(ij))
        self.assertTrue(any(len(each) == 2 for each in copies))

    def test_blockDetail(self):
        detail = self.db.blockDetail("c00n01", 0)
        self.assertIn("pdens", detail["params"])
        self.assertIn("flags", detail["params"])

    def test_settingsBringTheirResults(self):
        """A case settings file opens the database its run wrote, found by case title."""
        settings = os.path.join(self.scratch, "small.yaml")
        with open(settings, "w") as stream:
            stream.write("settings:\n  nCycles: 2\n")
        session = Session()
        status, _payload = session.open({"path": settings})
        try:
            self.assertEqual(status, 200)
            self.assertTrue(session.state()["results"]["open"])
            self.assertEqual(session.state()["results"]["path"], str(self.db.path))
        finally:
            session.results.close()

    def test_rejectsSomethingThatIsNotADatabase(self):
        bogus = os.path.join(self.scratch, "bogus.h5")
        with open(bogus, "w") as stream:
            stream.write("not hdf5")
        with self.assertRaises(results.ResultsError):
            results.ResultsDatabase(bogus)


class TestHelpers(unittest.TestCase):
    def test_jaggedOwnersSkipEmptyObjects(self):
        """Offsets only cover objects with values, so object 1 here owns nothing."""
        with tempfile.TemporaryDirectory() as scratch:
            with h5py.File(os.path.join(scratch, "j.h5"), "w") as h5:
                dataset = h5.create_dataset("x", data=np.arange(5.0))
                dataset.attrs["offsets"] = np.array([0, 2])
                dataset.attrs["noneLocations"] = np.array([1])
                np.testing.assert_array_equal(results.jaggedOwners(dataset), [0, 0, 2, 2, 2])

    def test_decodeFlags(self):
        packed = np.array([[0b101, 0], [0b010, 1]], dtype=np.uint8)
        order = [f"F{k}" for k in range(9)]
        self.assertEqual(results.decodeFlags(packed, order), ["F0 F2", "F1 F8"])


class TestResultsOverHttp(_WithDatabase):
    def setUp(self):
        self.server = GuiServer(Session.forPath(self.path)).start()

    def tearDown(self):
        self.server.session.results.close()
        self.server.stop()

    def get(self, path):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.port, timeout=30)
        connection.request("GET", path, headers={TOKEN_HEADER: self.server.token})
        response = connection.getresponse()
        return response.status, response.getheader("Content-Type"), response.read()

    def test_openingADatabaseOpensResults(self):
        state = self.server.session.state()
        self.assertTrue(state["results"]["open"])
        self.assertFalse(state["blueprints"]["open"])

    def test_seriesArrivesAsFloats(self):
        status, contentType, body = self.get("/api/results/series?level=block&param=pdens")
        self.assertEqual(status, 200)
        self.assertEqual(contentType, "application/octet-stream")
        total = sum(len(self.db.topologyOf(key).blockRows) for key in self.db.stepKeys())
        self.assertEqual(len(body), 4 * total)

    def test_unknownNodeIsAnError(self):
        status, _contentType, _body = self.get("/api/results/components?step=c99n99&param=temperatureInC")
        self.assertEqual(status, 400)


if __name__ == "__main__":
    unittest.main()
