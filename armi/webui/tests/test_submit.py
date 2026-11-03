"""Tests for submitting the open case to a runner and following it."""

import pathlib
import time
import unittest
from unittest import mock

from armi import configure, isConfigured

if not isConfigured():
    configure()

from armi.cases import runners
from armi.utils import caseWorker as worker
from armi.webui import submit
from armi.webui.tests.helpers import inTempDir

LOG = """[info] Opening database file at /fast/case.h5
[impt] Beginning of cycle 0
[info] Writing to database for statepoint: /c00n00
[impt] Critical rod search, cycle 0 node 1: banks 1, 2 at 56.4% inserted
[info] Writing to database for statepoint: /c00n01
"""


class FakeRunner:
    """Stands in for a suite runner: writes a worker log, then reports what it is told to."""

    def __init__(self, status="ok", hold=0.0):
        self.status = status
        self.hold = hold
        self.stopped = []

    def run(self, suite):
        (case,) = list(suite)
        pathlib.Path(case.directory, worker.LOG_FILE).write_text(LOG)
        end = time.time() + self.hold
        while time.time() < end and not self.stopped:
            time.sleep(0.02)
        status = "stopped" if self.stopped else self.status
        return {case.title: {"status": status, "outcome": status}}

    def requestStop(self, directory):
        self.stopped.append(directory)


def _wait(submission, timeout=10.0):
    end = time.time() + timeout
    while submission.running and time.time() < end:
        time.sleep(0.02)


class TestSubmission(unittest.TestCase):
    def _submit(self, runner, settings="settings:\n  nCycles: 1\n  burnSteps: 2\n"):
        path = pathlib.Path("case.yaml")
        path.write_text(settings)
        with mock.patch.object(submit, "makeRunner", return_value=runner):
            return submit.Submission(path, {"kind": "local"})

    def test_progressComesFromTheLog(self):
        with inTempDir():
            run = self._submit(FakeRunner())
            _wait(run)
            status = run.read(0)
            self.assertEqual(status["state"], "finished")
            self.assertEqual(status["progress"]["nodes"], 2)
            self.assertEqual(status["progress"]["totalNodes"], 3)
            self.assertEqual(status["progress"]["lastNode"], [0, 1])
            self.assertIn("56.4% inserted", status["progress"]["latest"])
            self.assertEqual(run.read(status["offset"])["text"], "")
            self.assertIsNone(status["database"])

    def test_anEarlierRunsLogIsNotThisOnes(self):
        with inTempDir():
            pathlib.Path(worker.LOG_FILE).write_text("[info] Writing to database for statepoint: /c00n00\n")

            class Silent(FakeRunner):
                def run(self, suite):
                    return {next(iter(suite)).title: {"status": "ok", "outcome": "cached"}}

            run = self._submit(Silent())
            _wait(run)
            status = run.read(0)
            self.assertEqual(status["state"], "cached")
            self.assertEqual(status["progress"]["nodes"], 0)

    def test_stopAsksTheRunner(self):
        with inTempDir():
            runner = FakeRunner(hold=10.0)
            run = self._submit(runner)
            time.sleep(0.1)
            self.assertEqual(run.state, "running")
            run.stop()
            _wait(run)
            self.assertEqual(runner.stopped, [str(pathlib.Path.cwd().resolve())])
            self.assertEqual(run.state, "stopped")

    def test_failureCarriesTheWorkersError(self):
        with inTempDir():
            pathlib.Path(worker.RESULT_FILE).write_text('{"status": "failed", "error": "Traceback\\nValueError: no"}')
            run = self._submit(FakeRunner(status="failed"))
            _wait(run)
            self.assertEqual(run.read()["state"], "failed")
            self.assertEqual(run.read()["error"], "ValueError: no")


class TestTargets(unittest.TestCase):
    def test_runnersForEachKind(self):
        self.assertIsInstance(submit.makeRunner({"kind": "local"}), runners.LocalRunner)
        ssh = submit.makeRunner({"kind": "ssh", "host": "me@there", "remoteRoot": "/scratch", "setup": "source e"})
        self.assertIsInstance(ssh, runners.SshRunner)
        self.assertEqual(ssh.syncEvery, submit.SYNC_SECONDS)
        self.assertEqual(ssh.setup, "source e")
        slurm = submit.makeRunner({"kind": "slurm", "cpus": "4", "sbatchArgs": "--partition=short --mem=8G"})
        self.assertIsInstance(slurm, runners.SlurmRunner)
        self.assertEqual(slurm.cpusPerJob, 4)
        self.assertEqual(slurm.sbatchArgs, ["--partition=short", "--mem=8G"])
        self.assertEqual(submit.makeRunner({"kind": "local", "timeoutHours": "2"}).timeout, 7200.0)

    def test_badTargetsAreRefused(self):
        with self.assertRaises(ValueError):
            submit.makeRunner({"kind": "ssh", "host": "", "remoteRoot": "/x"})
        with self.assertRaises(ValueError):
            submit.makeRunner({"kind": "carrier pigeon"})
