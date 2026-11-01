"""Tests for running a case from the editor."""

import pathlib
import sys
import time
import unittest

from armi import configure, isConfigured

if not isConfigured():
    configure()

from armi.webui import runs
from armi.webui.api import Session
from armi.webui.tests.helpers import inTempDir


def _waitFor(run, timeout=20.0):
    end = time.time() + timeout
    while run.running and time.time() < end:
        time.sleep(0.05)


class TestCaseRun(unittest.TestCase):
    def test_followsTheLogToTheEnd(self):
        with inTempDir() as scratch:
            tmp = pathlib.Path(scratch)
            settings = tmp / "case.yaml"
            settings.write_text("settings: {}\n")
            run = runs.CaseRun(settings, [sys.executable, "-c", "print('hello'); print('there')"])
            _waitFor(run)
            status = run.read(0)
            self.assertEqual(status["state"], "finished")
            self.assertEqual(status["returncode"], 0)
            self.assertIn("hello\nthere", status["text"])
            self.assertEqual(run.read(status["offset"])["text"], "")
            self.assertTrue((tmp / "case.gui-run.log").exists())

    def test_aFailureIsAFailure(self):
        with inTempDir() as scratch:
            tmp = pathlib.Path(scratch)
            settings = tmp / "case.yaml"
            settings.write_text("settings: {}\n")
            run = runs.CaseRun(settings, [sys.executable, "-c", "raise SystemExit(3)"])
            _waitFor(run)
            self.assertEqual(run.state, "failed")
            self.assertEqual(run.read()["returncode"], 3)

    def test_stopEndsTheRun(self):
        with inTempDir() as scratch:
            tmp = pathlib.Path(scratch)
            settings = tmp / "case.yaml"
            settings.write_text("settings: {}\n")
            run = runs.CaseRun(settings, [sys.executable, "-c", "import time; time.sleep(60)"])
            self.assertEqual(run.state, "running")
            run.stop()
            self.assertEqual(run.state, "stopped")

    def test_launchRunsTheConfiguredApp(self):
        command = runs.launchCommand("case.yaml")
        self.assertEqual(command[0], sys.executable)
        self.assertEqual(command[-2:], ["run", "case.yaml"])


class TestSessionRuns(unittest.TestCase):
    def test_needsSavedSettings(self):
        session = Session()
        status, payload = session.startRun({})
        self.assertEqual(status, 400)
        self.assertIn("settings", payload["error"])

    def test_refusesUnsavedEdits(self):
        with inTempDir() as scratch:
            tmp = pathlib.Path(scratch)
            settings = tmp / "case.yaml"
            settings.write_text("settings:\n  nCycles: 1\n")
            session = Session(csPath=settings)
            status, _ = session.putSettings({"values": {"nCycles": 2}})
            self.assertEqual(status, 200)
            self.assertTrue(session.state()["settings"]["dirty"])
            status, payload = session.startRun({})
            self.assertEqual(status, 409)
            self.assertIn("Save", payload["error"])

    def test_statusBeforeAnyRun(self):
        status, payload = Session().runStatus({})
        self.assertEqual(status, 200)
        self.assertIsNone(payload["state"])
