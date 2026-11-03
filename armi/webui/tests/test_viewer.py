"""Tests for reading results databases quickly, checked against ARMI's own loader."""

import http.client
import json
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
from armi.physics.economics import fuelCost
from armi.webui import results
from armi.webui.api import Session
from armi.webui.fuelcycle import FuelCycleFromResults
from armi.webui.server import TOKEN_HEADER, GuiServer
from armi.webui.syntheticResults import writeSyntheticDatabase

_SHARED = {}


def setUpModule():
    """One small database for every test here: two cycles of two nodes, third core, fuel reloaded once."""
    scratch = tempfile.mkdtemp(prefix="armi-webui-results-")
    path = os.path.join(scratch, "small.h5")
    writeSyntheticDatabase(path, years=2, nodesPerYear=2, fullCore=False, split=False, componentTemperatures=False)
    _SHARED.update(scratch=scratch, path=path)


def tearDownModule():
    shutil.rmtree(_SHARED["scratch"], ignore_errors=True)


class _WithDatabase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.scratch = _SHARED["scratch"]
        cls.path = _SHARED["path"]
        cls.db = results.ResultsDatabase(cls.path)

    @classmethod
    def tearDownClass(cls):
        cls.db.close()

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
        core = {entry["name"]: entry for entry in summary["core"]}
        self.assertEqual(core["keff"]["level"], "core")
        self.assertEqual(len(core["keff"]["values"]), 4)
        self.assertEqual(core["time"]["level"], "reactor")
        self.assertNotIn("serialNum", core)

    def test_coreHistoryMatchesArmi(self):
        keff = next(entry for entry in self.db.coreHistory() if entry["name"] == "keff")
        for key, value in zip(self.db.stepKeys(), keff["values"]):
            self.assertAlmostEqual(value, self.loaded(key).core.p.keff)
        self.assertGreater(keff["values"][0], keff["values"][1])

    def test_shufflingChangesTheFrameButNotTheTopology(self):
        frames = [self.db.positionsOf(key)[0] for key in self.db.stepKeys()]
        self.assertEqual(frames[0], frames[1])
        self.assertEqual(frames[2], frames[3])
        self.assertNotEqual(frames[0], frames[2])
        topologies = [self.db.topologyOf(key) for key in self.db.stepKeys()]
        self.assertEqual(len({topo.structureKey for topo in topologies}), 1)

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

    def test_freshFuelGetsItsOwnTopology(self):
        """Fresh fuel loaded in place of discharged fuel changes the names but shares the structure."""
        before, after = self.db.topologyOf("c00n01"), self.db.topologyOf("c01n00")
        self.assertNotEqual(before.id, after.id)
        self.assertEqual(before.structureKey, after.structureKey)
        self.assertTrue(set(after.assemblyNames) - set(before.assemblyNames))
        self.assertIs(before.parent, after.parent)
        names = self.db.names(after.id)
        self.assertEqual(len(names["blockNames"]), len(after.blockRows))


class TestFuelCycleFromResults(_WithDatabase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.fuelCycle = FuelCycleFromResults(cls.db)

    def assertSameGroups(self, actual, expected):
        self.assertEqual([(g.form, round(g.assay, 5)) for g in actual], [(g.form, round(g.assay, 5)) for g in expected])
        for mine, theirs in zip(actual, expected):
            self.assertAlmostEqual(mine.heavyMetal, theirs.heavyMetal, delta=1e-6 * theirs.heavyMetal)
            self.assertAlmostEqual(mine.uranium, theirs.uranium, delta=1e-6 * theirs.uranium)
            self.assertAlmostEqual(mine.u235, theirs.u235, delta=1e-6 * theirs.u235)

    def test_initialCoreWeighsTheSameAsThePlugin(self):
        with Database(self.path, "r") as db:
            r = db.load(0, 0)
        self.assertSameGroups(self.fuelCycle.inventory("c00n00", "auto"), fuelCost.fuelInventory(r.core, "auto"))

    def test_reloadsAreTheFreshAssembliesTheRunLoaded(self):
        with Database(self.path, "r") as db:
            first, second = db.load(0, 0), db.load(1, 0)
        seen = {a.getName() for a in first.core}
        fresh = [a for a in second.core if a.getName() not in seen and fuelCost.isFresh(a)]
        self.assertTrue(fresh)
        lc, _cs = self.fuelCycle.cost()
        self.assertEqual(lc.source, "run")
        self.assertEqual(len(lc.reloads), 1)
        self.assertSameGroups(lc.reloads[0].groups, fuelCost.fuelInventory(second.core, "auto", fresh))

    def test_changingAPriceChangesTheCost(self):
        base = self.fuelCycle.report()
        dearer = self.fuelCycle.report({"econSwuCost": 2 * self.fuelCycle.cs["econSwuCost"]})
        self.assertGreater(dearer["lcoe"], base["lcoe"])
        swu = next(entry for entry in dearer["inputs"] if entry["name"] == "econSwuCost")
        self.assertEqual(swu["value"], 2 * swu["caseValue"])
        self.assertEqual(len(base["loads"]), 2)
        self.assertTrue(base["keyPoints"])

    def test_onlyEconomicsInputsCanChange(self):
        with self.assertRaises(results.ResultsError):
            self.fuelCycle.settingsWith({"power": 1.0})

    def test_pdf(self):
        path = os.path.join(self.scratch, "cost.pdf")
        self.fuelCycle.pdf(path)
        with open(path, "rb") as stream:
            self.assertEqual(stream.read(4), b"%PDF")


class TestHelpers(unittest.TestCase):
    def test_jaggedOwnersSkipEmptyObjects(self):
        """Offsets only cover objects with values, so object 1 here owns nothing."""
        with tempfile.TemporaryDirectory() as scratch:
            with h5py.File(os.path.join(scratch, "j.h5"), "w") as h5:
                dataset = h5.create_dataset("x", data=np.arange(5.0))
                dataset.attrs["offsets"] = np.array([0, 2])
                dataset.attrs["noneLocations"] = np.array([1])
                np.testing.assert_array_equal(results.jaggedOwners(dataset), [0, 0, 2, 2, 2])

    def test_perInstanceValuesOfAJaggedParameter(self):
        """One value per instance, stored ragged: each component's values, None for one without any."""
        with tempfile.TemporaryDirectory() as scratch:
            with h5py.File(os.path.join(scratch, "instances.h5"), "w") as h5:
                group = h5.create_group("Circle")
                group.create_dataset("mult", data=np.array([3.0, 1.0, 2.0]))
                dataset = group.create_dataset("pdens", data=np.array([1.0, 2.0, 3.0, 10.0, 20.0]))
                dataset.attrs["jagged"] = True
                dataset.attrs["offsets"] = np.array([0, 3])
                dataset.attrs["shapes"] = np.array([[3], [2]])
                dataset.attrs["noneLocations"] = np.array([1])
                values = results.perObject(dataset)
                self.assertEqual(len(values), 3)
                np.testing.assert_array_equal(values[0], [1.0, 2.0, 3.0])
                self.assertIsNone(values[1])
                np.testing.assert_array_equal(values[2], [10.0, 20.0])
                self.assertTrue(results.isPerInstance("pdens", group))
                # The same values with lengths that are not the multiplicities are something else.
                group["mult"][...] = [3.0, 1.0, 5.0]
                self.assertFalse(results.isPerInstance("pdens", group))

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

    def test_fuelCostOverHttp(self):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.port, timeout=60)
        body = json.dumps({"overrides": {"econDiscountRate": 0.1}})
        connection.request("POST", "/api/fuelcost", body=body, headers={TOKEN_HEADER: self.server.token})
        response = connection.getresponse()
        payload = json.loads(response.read())
        self.assertEqual(response.status, 200)
        self.assertAlmostEqual(payload["discountRate"], 0.1)

        connection.request("POST", "/api/fuelcost/pdf", body="{}", headers={TOKEN_HEADER: self.server.token})
        response = connection.getresponse()
        self.assertEqual(response.getheader("Content-Type"), "application/pdf")
        self.assertIn("attachment", response.getheader("Content-Disposition"))
        self.assertEqual(response.read(4), b"%PDF")

    def test_unknownNodeIsAnError(self):
        status, _contentType, _body = self.get("/api/results/components?step=c99n99&param=temperatureInC")
        self.assertEqual(status, 400)


if __name__ == "__main__":
    unittest.main()
