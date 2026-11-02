"""Tests for branch calculations on perturbed copies of a reactor."""

import os
import subprocess
import time
import unittest
from unittest import mock

from armi import context
from armi.cases import branches
from armi.cases.branches import (
    ForkExecutor,
    FunctionStateModifier,
    MpiExecutor,
    SerialExecutor,
    SnapshotExecutor,
    StateModifier,
    runBranches,
)
from armi.cases.inputModifiers.inputModifiers import SettingsModifier
from armi.cases.runners import LocalRunner
from armi.cases.sweeps import Sweep
from armi.testing import loadTestReactor
from armi.utils import caseWorker as worker
from armi.utils.directoryChangers import TemporaryDirectoryChanger


class SetPower(StateModifier):
    def __init__(self, power):
        StateModifier.__init__(self, {"power": power})
        self.power = power

    def __call__(self, r):
        r.core.p.power = self.power


def scaleFirstBlock(r, factor):
    b = r.core.getFirstBlock()
    b.p.power = factor


def reportPower(r, cs):
    with open("deck.txt", "w") as f:
        f.write(str(r.core.p.power))
    return {"power": r.core.p.power, "blockPower": r.core.getFirstBlock().p.power, "pid": os.getpid()}


def failing(r, cs):
    raise RuntimeError("deliberate failure")


def sleepy(r, cs):
    with open("grandchild.pid", "w") as f:
        f.write(str(subprocess.Popen(["sleep", "60"]).pid))
    time.sleep(60)
    return {}


class BranchTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.o, cls.r = loadTestReactor()
        cls.cs = cls.o.cs
        cls.r.core.p.power = 1.0
        cls.r.core.getFirstBlock().p.power = 1.0

    def setUp(self):
        self.tempDir = TemporaryDirectoryChanger()
        self.tempDir.__enter__()

    def tearDown(self):
        self.tempDir.__exit__(None, None, None)

    def sweep(self):
        return Sweep.axis(SetPower(p) for p in (2.0, 3.0)) * Sweep.axis(
            FunctionStateModifier(scaleFirstBlock, factor=f) for f in (10.0, 20.0)
        )

    def assertPowerResults(self, results):
        self.assertEqual(list(results), ["branch000", "branch001", "branch002", "branch003"])
        self.assertEqual([r["status"] for r in results.values()], ["ok"] * 4)
        self.assertEqual([r["results"]["power"] for r in results.values()], [2.0, 2.0, 3.0, 3.0])
        self.assertEqual([r["results"]["blockPower"] for r in results.values()], [10.0, 20.0, 10.0, 20.0])
        # every branch ran in its own directory, and none touched the reactor the run goes on with
        with open(os.path.join("branches", "branch002", "deck.txt")) as f:
            self.assertEqual(float(f.read()), 3.0)
        self.assertEqual(self.r.core.p.power, 1.0)
        self.assertEqual(self.r.core.getFirstBlock().p.power, 1.0)


class TestSerialAndCommon(BranchTestCase):
    def test_serialBranchesAndTable(self):
        results = runBranches(self.r, self.cs, self.sweep(), reportPower)
        self.assertPowerResults(results)
        table = results.table()
        self.assertEqual(table[1]["case"], "branch001")
        self.assertEqual((table[1]["power"], table[1]["factor"]), (2.0, 20.0))
        results.writeTable("table.csv")
        with open("table.csv") as f:
            self.assertEqual(f.readline().strip(), "case,status,wallSeconds,power,factor,blockPower,pid")
        self.assertEqual(results.failed(), [])

    def test_failuresAreRecorded(self):
        results = runBranches(self.r, self.cs, [SetPower(5.0)], f"{__name__}:failing", names=lambda i, p: "bad")
        self.assertEqual(results["bad"]["status"], "failed")
        self.assertIn("deliberate failure", results["bad"]["error"])
        self.assertEqual(results.failed(), ["bad"])

    def test_unserializableResultsFail(self):
        results = runBranches(self.r, self.cs, [SetPower(5.0)], lambda r, cs: {"obj": object()})
        self.assertEqual(results["branch000"]["status"], "failed")
        self.assertIn("JSON", results["branch000"]["error"])

    def test_rejectsInputModifiersAndDuplicateNames(self):
        with self.assertRaises(TypeError):
            runBranches(self.r, self.cs, [SettingsModifier("power", 1.0)], reportPower)
        with self.assertRaises(ValueError):
            runBranches(self.r, self.cs, [SetPower(1.0), SetPower(2.0)], reportPower, names=lambda i, p: "same")

    def test_mpiWithoutMpiRunsSerially(self):
        results = runBranches(self.r, self.cs, self.sweep(), reportPower, MpiExecutor())
        self.assertPowerResults(results)
        self.assertEqual({r["results"]["pid"] for r in results.values()}, {os.getpid()})


class TestForkExecutor(BranchTestCase):
    def test_forkedBranches(self):
        # forked children inherit the task, so even a closure works
        results = runBranches(self.r, self.cs, self.sweep(), lambda r, cs: reportPower(r, cs), ForkExecutor(3))
        self.assertPowerResults(results)
        pids = {r["results"]["pid"] for r in results.values()}
        self.assertEqual(len(pids), 4)
        self.assertNotIn(os.getpid(), pids)

    def test_timeoutKillsTheBranchAndItsCodes(self):
        executor = ForkExecutor(maxWorkers=2, timeout=2)
        results = runBranches(self.r, self.cs, [SetPower(1.0), SetPower(2.0)], sleepy, executor)
        self.assertEqual([r["status"] for r in results.values()], ["timeout", "timeout"])
        with open(os.path.join("branches", "branch000", "grandchild.pid")) as f:
            pid = int(f.read())
        time.sleep(0.5)
        self.assertFalse(os.path.exists(f"/proc/{pid}") and "sleep" in open(f"/proc/{pid}/cmdline").read())

    def test_crashedChildIsAFailure(self):
        results = runBranches(self.r, self.cs, [SetPower(1.0)], lambda r, cs: os._exit(3), ForkExecutor(1))
        self.assertEqual(results["branch000"]["status"], "failed")
        self.assertIn("exit code 3", results["branch000"]["error"])

    def test_refusesUnderMpi(self):
        with mock.patch.object(context, "MPI_SIZE", 2), self.assertRaises(RuntimeError):
            runBranches(self.r, self.cs, [SetPower(1.0)], reportPower, ForkExecutor(1))


class TestSnapshotExecutor(BranchTestCase):
    def test_branchesAsLocalJobs(self):
        results = runBranches(self.r, self.cs, self.sweep(), f"{__name__}:reportPower", LocalRunner(maxWorkers=4))
        self.assertPowerResults(results)
        self.assertNotIn(os.getpid(), {r["results"]["pid"] for r in results.values()})
        directory = os.path.join("branches", "branch000")
        snapshot = os.path.join(directory, branches.SNAPSHOT_FILE)
        self.assertEqual(os.stat(snapshot).st_ino, os.stat(os.path.join("branches", branches.SNAPSHOT_FILE)).st_ino)
        self.assertEqual(worker.readJob(directory)["kind"], "branch")

    def test_timeoutAndFailure(self):
        executor = SnapshotExecutor(LocalRunner(maxWorkers=2), timeout=5)
        results = runBranches(self.r, self.cs, [SetPower(1.0)], sleepy, executor)
        self.assertEqual(results["branch000"]["status"], "timeout")
        results = runBranches(self.r, self.cs, [SetPower(1.0)], failing, executor)
        self.assertIn("deliberate failure", results["branch000"]["error"])

    def test_unpicklableTask(self):
        with self.assertRaises(TypeError):
            runBranches(self.r, self.cs, [SetPower(1.0)], lambda r, cs: {}, LocalRunner(maxWorkers=1))


class TestSerialCopies(BranchTestCase):
    def test_copyStateIsIndependent(self):
        rCopy, csCopy = branches.copyState(self.r, self.cs)
        rCopy.core.p.power = 99.0
        self.assertEqual(self.r.core.p.power, 1.0)
        self.assertIs(rCopy.core.getFirstBlock().core.r, rCopy)
        self.assertEqual(csCopy["power"], self.cs["power"])
        results = SerialExecutor().run(self.r, self.cs, [], reportPower, os.getcwd())
        self.assertEqual(results, {})
